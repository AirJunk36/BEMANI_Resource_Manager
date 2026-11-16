# BEMANI Resource Manager

**1.0.0 Beta** · [简体中文](README.md) · [GPL-3.0](LICENSE)

**This project was ENTIRELY made by Codex**

An unofficial desktop browser and editor for local BEMANI resources.
SwiftUI on macOS and WinUI 3 on Windows share a Python parsing core.

Features include legacy BIN/IDX/GCZ and IFS animation previews, audio and
BGA playback, song/composer search, complete-song previews, WAV/original
audio/MP4/GIF export, per-material visibility, picking, replacement and undo.
Original game files are protected from overwrite.

19th CN encrypted 2DX banks and charts support unpacking, playback and
copy-based replacement. Full-song previews have an All button for BGA and
Layer. Generated export names are English; replacement copies use `_replaced`.
GIF exports preserve transparency at 50 FPS without audio.
See [Layer playback](docs/LAYER_PLAYBACK.md) for judgement modes and version differences.

The interface supports Chinese and English, CPU/GPU rendering, 60/120 FPS,
and selectable resolution/aspect ratio. Optional Live2D/MOC3 editing and
preview require separately licensed components; read [the Live2D notes](docs/LIVE2D.md).

## Build

This repository contains source, not game data, game DLLs, Cubism Core,
private application artwork or the previous private app bundles.
Default icons are generated from project-owned drawing code.

On macOS, install Python (3.13/3.14 recommended), the **macOS 26.0 SDK** and
Swift 6.2 or newer. The deployment target is macOS 14.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/build_icons.py
.venv/bin/python tools/build_macos.py
./start_mac.command
```

Use `IIDX_MACOS_SDK` and `IIDX_SWIFTC` for a separate toolchain.
On Windows, install x64 Python, .NET 8 SDK and Windows development tools:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools\build_icons.py
dotnet build native\windows\IIDXResourceViewer.csproj -c Release -p:Platform=x64 -p:RuntimeIdentifier=win-x64
$env:IIDX_VIEWER_HOME = (Get-Location).Path
.\build\windows\IIDXResourceViewer.exe
```

For the ARM64 frontend, use `Platform=ARM64` and `RuntimeIdentifier=win-arm64`.
Windows ARM uses x64 Python for parsing. Run tests with `python -m unittest
discover -s tests -v`; private-fixture tests are skipped when unavailable.
Set `IIDX_GAME_ROOT` / `IIDX_LIVE2D_ROOT` to your resource directories.
GPU tests need a working graphics session. See the Chinese README for
regression scripts and shader rebuilding.

## License

Original project code is **GPL-3.0-only**, Copyright © 2026 AirJunk36 and
contributors, without warranty. See [LICENSE](LICENSE), [COPYRIGHT](COPYRIGHT)
and [third-party notices](THIRD_PARTY_NOTICES.md).
No Cubism linking exception is granted. Binary redistribution involving
proprietary Cubism Core remains a separate licensing issue.
