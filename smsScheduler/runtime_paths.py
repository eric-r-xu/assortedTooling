"""Paths shared by the source app and its self-contained macOS bundle."""
import os
import sys
from pathlib import Path


def bundled():
    return bool(getattr(sys, "frozen", False))


def data_home():
    default = (Path.home() / "Library/Application Support/SMS & WeChat Scheduler"
               if bundled() else Path(__file__).resolve().parent)
    return Path(os.environ.get("SMS_SCHED_HOME", default)).expanduser().resolve()


def runner_command():
    if bundled():
        return [sys.executable, "--runner"]
    return [sys.executable, str(Path(__file__).with_name("runner.py"))]
