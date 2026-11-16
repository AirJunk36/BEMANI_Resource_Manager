"""Sign a fresh bundle before publishing it, including on NTFS volumes."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import tempfile
from bundle_macos_core import embed_core

home = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("IIDX_MACOS_APP", home / "BEMANI素材管理器.app")).expanduser().resolve()
app.parent.mkdir(parents=True, exist_ok=True)
binary = Path(os.environ.get("IIDX_MACOS_BINARY",home / "build/macos/IIDXResourceViewer"))
info_path = app / "Contents/Info.plist"
previous = plistlib.loads(info_path.read_bytes()) if info_path.exists() else {}
build_version = str(int(previous.get("CFBundleVersion", "0")) + 1)
display_version = (home / 'VERSION').read_text().strip()
bundle_id = "local.iidx.resourceviewer"

with tempfile.TemporaryDirectory(prefix="bundle-", dir=app.parent, ignore_cleanup_errors=True) as staging:
    staged_app = Path(staging) / app.name
    contents = staged_app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    for language,title in [('en','BEMANI Resource Manager'),('zh-Hans','BEMANI素材管理器')]:
        localized=contents/'Resources'/(language+'.lproj');localized.mkdir()
        (localized/'InfoPlist.strings').write_text('"CFBundleDisplayName" = "'+title+'";\n"CFBundleName" = "'+title+'";\n')

    shutil.copy2(home / "native/assets/Application.icns", contents / "Resources/Application.icns")
    shutil.copy2(binary, contents / "MacOS/IIDXResourceViewer")
    embed_core(home, contents / "Resources")
    for name in ("LICENSE","COPYRIGHT","THIRD_PARTY_NOTICES.md"):
        shutil.copy2(home/name, contents/"Resources"/name)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleExecutable": "IIDXResourceViewer", "CFBundleIdentifier": bundle_id, "CFBundleName": "BEMANI素材管理器", "CFBundleDisplayName": "BEMANI素材管理器", "CFBundleIconFile": "Application.icns", "CFBundleLocalizations": ["zh-Hans", "en"], "CFBundleDevelopmentRegion": "zh-Hans", "CFBundlePackageType": "APPL", "CFBundleShortVersionString": display_version.split()[0], "BEMANIDisplayVersion": display_version, "CFBundleGetInfoString": display_version, "CFBundleVersion": build_version, "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication"}, f)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", bundle_id, str(staged_app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged_app)], check=True)
    # Replacing an open executable in place creates NTFS ghost files inside the
    # bundle. Move the old directory aside so the new signature stays intact.
    old_app = Path(staging) / "previous.app"
    if app.exists():
        app.rename(old_app)
    try:
        staged_app.rename(app)
    except OSError:
        if old_app.exists():
            old_app.rename(app)
        raise

temporary = binary.with_suffix(".new")
shutil.copy2(app / "Contents/MacOS/IIDXResourceViewer", temporary)
os.replace(temporary, binary)
os.utime(app, None)
register = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
if register.exists():
    subprocess.run([str(register), "-f", str(app)], check=True)
print(app)
