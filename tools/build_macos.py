"""Build an adjacent native SwiftUI app; no game files are touched."""
from pathlib import Path
import platform
import subprocess
import tempfile
import runpy
import os
import shlex
import json
import shutil

home = Path(__file__).resolve().parents[1]
sources = sorted((home / "native/macos").glob("*.swift"))
build = home / "build/macos"
build.mkdir(parents=True, exist_ok=True)
binary = Path(os.environ.get("IIDX_MACOS_BINARY",build / "IIDXResourceViewer"))
binary.parent.mkdir(parents=True,exist_ok=True)
target = platform.machine() + "-apple-macosx14.0"
configuration = json.loads((build / "toolchain.json").read_text()) if (build / "toolchain.json").exists() else {}
compiler = os.environ.get("IIDX_SWIFTC", configuration.get("swiftc", "swiftc"))
sdk_path = os.environ.get("IIDX_MACOS_SDK", configuration.get("sdk"))
if not sdk_path:
    result = subprocess.run(["xcrun", "--sdk", "macosx26.0", "--show-sdk-path"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("需要 macOS 26.0 SDK；可用 IIDX_MACOS_SDK 指定目录。")
    sdk_path = result.stdout.strip()
sdk = Path(sdk_path).resolve()
settings = json.loads((sdk / "SDKSettings.json").read_text())
if settings["Version"] != "26.0":
    raise SystemExit(f"需要 macOS 26.0 SDK，当前为 {settings['Version']}。")
with tempfile.TemporaryDirectory(prefix="iidx-swift-build-") as directory:
    flags = shlex.split(os.environ.get("IIDX_SWIFT_FLAGS", ""))
    flags += ["-Xlinker", "-platform_version", "-Xlinker", "macos", "-Xlinker", "14.0", "-Xlinker", settings["Version"]]
    maps = Path("/Library/Developer/CommandLineTools/usr/include/swift")
    if all((maps / name).exists() for name in ("module.modulemap", "bridging.modulemap")):
        empty = Path(directory) / "empty.modulemap"; empty.write_text("")
        overlay = Path(directory) / "overlay.json"
        overlay.write_text(json.dumps({"version": 0, "roots": [{"type": "file", "name": str(maps / "module.modulemap"), "external-contents": str(empty)}]}))
        flags += ["-vfsoverlay", str(overlay)]
    compiled=Path(directory)/"IIDXResourceViewer"
    subprocess.run([compiler, "-O", "-whole-module-optimization", "-swift-version", "5", "-parse-as-library", "-sdk", str(sdk), "-module-cache-path", str(Path(tempfile.gettempdir()) / "iidx-swift26-module-cache"), *flags, "-target", target, "-o", str(compiled), *map(str, sources), "-framework", "SwiftUI", "-framework", "AppKit", "-framework", "AVKit", "-framework", "MediaPlayer"], check=True)
    # Link on the local filesystem; ld cannot reliably replace NTFS executables.
    staged=binary.with_suffix('.unsigned');shutil.copy2(compiled,staged);os.replace(staged,binary)
metadata = subprocess.check_output(["xcrun", "vtool", "-show-build", str(binary)], text=True)
if "sdk 26.0" not in metadata:
    raise SystemExit("链接器未记录 SDK 26.0；未发布应用。")
(build / "build-info.json").write_text(json.dumps({"sdk": settings["Version"], "sdk_path": str(sdk), "compiler": subprocess.check_output([compiler, "--version"], text=True).strip()}, indent=2))
runpy.run_path(str(home / "tools/bundle_macos.py"), run_name="__main__")
