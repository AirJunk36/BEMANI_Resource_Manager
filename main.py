"""Convenience launcher for the platform's native UI."""
from pathlib import Path
import os
import subprocess
import sys

home = Path(__file__).resolve().parent
if sys.platform == "darwin":
    executable = home / "BEMANI素材管理器.app/Contents/MacOS/IIDXResourceViewer"
    if not executable.exists():
        subprocess.run([sys.executable, str(home / "tools/build_macos.py")], check=True)
    os.execv(str(executable), [str(executable), "--home", str(home), *sys.argv[1:]])
elif sys.platform == "win32":
    executable = home / "build/windows/IIDXResourceViewer.exe"
    if not executable.exists():
        raise SystemExit("请先运行 start_windows.bat 构建 WinUI 3 界面。")
    os.environ["IIDX_VIEWER_HOME"] = str(home)
    raise SystemExit(subprocess.call([str(executable), *sys.argv[1:]]))
else:
    raise SystemExit("原生界面目前支持 macOS / Windows。backend.py 可单独用于资源解析。")
