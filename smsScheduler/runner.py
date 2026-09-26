"""Run by launchd: `runner.py <job_id>` sends a due job; `runner.py --check <phone>` dry-runs."""
import os
import sys
from datetime import datetime, timedelta

import scheduler
import store

GRACE = timedelta(hours=1)
CHECK_RESULT = store.HOME / "logs" / "check.txt"
WECHAT_CHECK_RESULT = store.HOME / "logs" / "check-wechat.txt"


def check(target, channel="sms"):
    job = {"channel": channel, "message": "", "thread": target, "phone": target}
    path = WECHAT_CHECK_RESULT if channel == "wechat" else CHECK_RESULT
    try:
        ok, out = scheduler.send_job(job, dry=True)
    except Exception as exc:
        ok, out = False, str(exc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{'OK' if ok else 'FAILED'}: {out}\n")
    scheduler.log(f"check {channel}: {out}")
    scheduler.remove_agent("check-wechat" if channel == "wechat" else "check")


def finish(job_id, status, result):
    with store.locked() as jobs:
        job = store.find(jobs, job_id)
        if job:
            job.update(status=status, result=result, finished=datetime.now().isoformat(timespec="seconds"))
    scheduler.log(f"{job_id} {status}: {result}")


def run(job_id, now=None):
    now = now or datetime.now()
    with store.locked() as jobs:
        job = store.find(jobs, job_id)
        if not job or job["status"] != "pending":
            state = job["status"] if job else "missing"
            if state == "sending":
                return  # Another invocation owns it; unloading would kill that sender.
            action = None
        else:
            late = now - datetime.fromisoformat(job["when"])
            if late < timedelta(0):
                return  # not due yet (load-time run, or same date in an earlier year)
            action = "send" if late <= GRACE else "missed"
            if action == "send":
                job["status"] = "sending"  # claimed, so a second trigger can't double-send
            payload = dict(job)

    if action is None:
        scheduler.log(f"{job_id} is {state}; removing agent")
    elif action == "missed":
        finish(job_id, "missed", f"not sent: {int(late.total_seconds() // 60)} min late (limit 60)")
    else:
        try:
            ok, out = scheduler.send_job(payload)
        except Exception as exc:
            ok, out = False, f"Sender error: {exc}. Check the messaging app before retrying."
        success = "submitted" if scheduler.channel(payload) == "wechat" else "sent"
        if ok and os.environ.get("SMS_SCHED_DRY"):
            success = "checked"
        finish(job_id, success if ok else "failed", out)
    scheduler.remove_agent(job_id)


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) == 2 and args[0] == "--check":
        check(args[1])
    elif len(args) == 2 and args[0] == "--check-wechat":
        check(args[1], "wechat")
    elif len(args) == 1 and not args[0].startswith("--"):
        run(args[0])
    else:
        raise SystemExit("Usage: --runner JOB_ID | --runner --check PHONE | --runner --check-wechat CHAT")


if __name__ == "__main__":
    main()
