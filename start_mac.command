#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if [[ -x 'BEMANI素材管理器.app/Contents/Resources/Core/bin/python3' ]]; then
  exec './BEMANI素材管理器.app/Contents/MacOS/IIDXResourceViewer' --home "$PWD" "$@"
fi
if ! command -v python3 >/dev/null 2>&1; then
  print "请先从 https://www.python.org/downloads/ 安装 Python 3.10 或更新版本。"
  read '?按回车退出'
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import ifstools, PIL, PySide6.QtGui, imageio_ffmpeg, Crypto.Cipher.AES, Crypto.Cipher.Blowfish, numpy' >/dev/null 2>&1; then
  print "首次运行：正在安装界面和资源解码依赖…"
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -f native/assets/Application.icns ]]; then .venv/bin/python tools/build_icons.py; fi
if [[ ! -x 'BEMANI素材管理器.app/Contents/MacOS/IIDXResourceViewer' ]]; then
  .venv/bin/python tools/build_macos.py
fi
exec './BEMANI素材管理器.app/Contents/MacOS/IIDXResourceViewer' --home "$PWD" "$@"
