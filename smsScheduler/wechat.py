"""Serialized, fail-closed WeChat desktop automation; no external dependencies."""
import fcntl
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import store

SCRIPT = Path(__file__).with_name("wechat.js")


@contextmanager
def _locked():
    store.HOME.mkdir(parents=True, exist_ok=True)
    with open(store.HOME / ".wechat.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another WeChat operation is running. This message was not sent; retry after it finishes.")
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _open_wechat():
    """Ask Launch Services to reopen the main window, even when the app is already running."""
    try:
        result = subprocess.run(["/usr/bin/open", "-a", "WeChat"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not open WeChat. Open its main Chats window and sign in, then try again.") from exc
    if result.returncode:
        raise RuntimeError("Could not open WeChat: " + (result.stderr.strip() or "check that WeChat is installed."))


def _call(mode, thread="", message=""):
    with _locked():
        _open_wechat()
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", str(SCRIPT), mode, thread],
                input=message if mode == "send" else "", capture_output=True, text=True, timeout=90,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("WeChat automation timed out. Check WeChat before retrying; sending may have started.")
        except OSError as exc:
            raise RuntimeError(f"Could not start WeChat automation: {exc}") from exc
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "WeChat automation failed. Check Accessibility and Automation permissions.")
        return result.stdout.strip()


def threads():
    try:
        result = json.loads(_call("list"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("WeChat returned an unreadable conversation list.") from exc
    if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
        raise RuntimeError("WeChat returned an invalid conversation list.")
    return result


def send(thread, message, dry=False):
    try:
        return True, _call("check" if dry else "send", thread, message)
    except RuntimeError as exc:
        return False, str(exc)
