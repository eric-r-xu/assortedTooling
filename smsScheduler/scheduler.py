"""Helpers for phone numbers, launchd agents, and sending through Messages."""
import os
import plistlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from store import HOME
from runtime_paths import bundled, runner_command

APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "send.applescript"
LOG = HOME / "logs" / "runner.log"
AGENTS = Path(os.environ.get("SMS_SCHED_AGENTS", Path.home() / "Library/LaunchAgents"))
PREFIX = "com.local.smswechatscheduler.job." if bundled() else "com.user.smssched."
NO_LAUNCHCTL = bool(os.environ.get("SMS_SCHED_NO_LAUNCHCTL"))
DOMAIN = f"gui/{os.getuid()}"


def normalize_phone(raw):
    """Return +E.164 for US numbers (10 or 11 digits) or explicit +numbers, else None."""
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def label(job_id):
    return PREFIX + job_id


def plist_path(job_id):
    return AGENTS / f"{label(job_id)}.plist"


def _launchctl(*args):
    if NO_LAUNCHCTL:
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _install(job_id, args, when=None):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "Label": label(job_id),
        "ProgramArguments": [*runner_command(), *args],
        "RunAtLoad": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    # launchd does not inherit the GUI's environment. Keep both processes on the
    # same data directory, including when a custom directory is used for tests.
    spec["EnvironmentVariables"] = {"SMS_SCHED_HOME": str(HOME)}
    for key in ("SMS_SCHED_DRY", "SMS_SCHED_AGENTS", "SMS_SCHED_NO_LAUNCHCTL"):
        if os.environ.get(key):
            spec["EnvironmentVariables"][key] = os.environ[key]
    if when:
        spec["StartCalendarInterval"] = {
            "Month": when.month, "Day": when.day, "Hour": when.hour, "Minute": when.minute,
        }
    AGENTS.mkdir(parents=True, exist_ok=True)
    path = plist_path(job_id)
    _launchctl("bootout", f"{DOMAIN}/{label(job_id)}")
    with open(path, "wb") as f:
        plistlib.dump(spec, f)
    r = _launchctl("bootstrap", DOMAIN, str(path))
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.returncode}")


def install_agent(job):
    _install(job["id"], [job["id"]], datetime.fromisoformat(job["when"]))


def install_check(phone):
    """One-off agent that dry-runs send.applescript from launchd (surfaces permission prompts)."""
    _install("check", ["--check", phone])


def install_wechat_check(thread):
    _install("check-wechat", ["--check-wechat", thread])


def channel(job):
    return job.get("channel", "sms")


def recipient(job):
    return job.get("thread", "") if channel(job) == "wechat" else job.get("phone", "")


def send_job(job, dry=False):
    kind = channel(job)
    if kind == "sms":
        return send(job["phone"], job["message"], dry=dry)
    if kind == "wechat":
        import wechat
        return wechat.send(job["thread"], job["message"], dry=dry or bool(os.environ.get("SMS_SCHED_DRY")))
    return False, f"Unknown message channel: {kind}"


def remove_agent(job_id):
    """Delete the plist, then unload. Bootout comes last because it kills the caller if it's the agent."""
    plist_path(job_id).unlink(missing_ok=True)
    _launchctl("bootout", f"{DOMAIN}/{label(job_id)}")


def agent_loaded(job_id):
    return _launchctl("print", f"{DOMAIN}/{label(job_id)}").returncode == 0


def send(phone, message, dry=False):
    """Send via Messages (SMS, falling back to iMessage). Returns (ok, output)."""
    if os.environ.get("SMS_SCHED_DRY"):
        dry = True
    if not dry:
        subprocess.run(["open", "-ga", "Messages"])
        time.sleep(5)
    args = ["/usr/bin/osascript", str(SCRIPT), phone] + ([] if dry else [message])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "osascript timed out (permission prompt waiting?)"
    out = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}")
    return r.returncode == 0, out
