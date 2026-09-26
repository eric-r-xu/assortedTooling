"""Build a self-contained Apple Silicon app and drag-to-Applications DMG."""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP_NAME = "SMS & WeChat Scheduler.app"
DMG_NAME = "SMS-WeChat-Scheduler-1.0.0-arm64.dmg"


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def main():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("Build this release on an Apple Silicon Mac with macOS 26 or newer.")
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build/pyinstaller-cache"))
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "SMSWeChatScheduler.spec", env=env)
    app = DIST / APP_NAME
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)

    stage = ROOT / "build/dmg-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    run("/usr/bin/ditto", app, stage / APP_NAME)
    (stage / "Applications").symlink_to("/Applications")
    shutil.copy2(ROOT / "README.md", stage / "README.md")
    shutil.copytree(ROOT / "docs/images", stage / "docs/images")
    dmg = DIST / DMG_NAME
    run("/usr/bin/hdiutil", "create", "-volname", "SMS & WeChat Scheduler",
        "-srcfolder", stage, "-format", "UDZO", "-ov", dmg)
    run("/usr/bin/hdiutil", "verify", dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    (DIST / (DMG_NAME + ".sha256")).write_text(f"{digest}  {DMG_NAME}\n")
    print(f"Built {dmg} ({dmg.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
