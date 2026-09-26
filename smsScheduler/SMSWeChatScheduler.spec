# Build using: .venv-build/bin/python build_dmg.py
import os

identity = os.environ.get("MACOS_SIGNING_IDENTITY") or None
a = Analysis(
    ["bundle_main.py"],
    pathex=[],
    binaries=[],
    datas=[("send.applescript", "."), ("wechat.js", ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="SMSWeChatScheduler", debug=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch="arm64",
    codesign_identity=identity, entitlements_file="packaging/entitlements.plist",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SMSWeChatScheduler")
app = BUNDLE(
    coll, name="SMS & WeChat Scheduler.app",
    bundle_identifier="com.local.smswechatscheduler",
    info_plist={
        "CFBundleDisplayName": "SMS & WeChat Scheduler",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "26.0",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Schedule messages using Messages and WeChat through System Events.",
    },
)
