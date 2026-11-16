# BEMANI Resource Manager

**BEMANI素材管理器 · 1.0.0 Beta**

[English](README.en.md) · [GPL-3.0](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md)

**本项目完全由Codex制作**

浏览、预览和编辑本地 BEMANI 游戏素材的非官方桌面工具。
macOS 使用 SwiftUI，Windows 使用 WinUI 3，共用 Python 解析核心。

## 功能

- IIDX 旧版 BIN/IDX/GCZ 和新版 IFS 的图像、动画读取与预览。
- SD9、2DX、S3P、S3V、SSP 等音频读取，以及 BGA 视频预览。
- 按曲名、作曲家、编号或路径搜索，显示曲名与作曲家。
- 完整乐曲试听，选择 BGA、Layer，导出 WAV、原编码音轨、MP4 或 GIF。
- 19th 中国版 AES 2DX 与配套谱面读取、试听、解包及副本替换。
- 完整乐曲预览支持一键选择 BGA / Layer。
- 素材显隐、预览点击选中、替换、撤销和另存；替换副本使用 `_replaced` 标题，生成的导出名称使用英文。原始游戏文件不被覆盖。
- CPU / GPU 渲染，60 / 120 FPS，多种分辨率、比例和中英文界面。
- 可选 Live2D/MOC3 字段、贴图、网格和变形器编辑，以及实时预览集成。运行库及分发条件见 [Live2D 说明](docs/LIVE2D.md)。

不同版本及特殊脚本的兼容性仍在完善。三维、滤镜及特定游戏宿主效果可能不完整。
Layer 判定、结尾及版本差异见 [Layer 播放说明](docs/LAYER_PLAYBACK.md)。
GIF 保留透明背景，使用 50 FPS，不包含音频。

## 从源码运行

仓库只提供源码，不包含游戏资源、游戏 DLL、Cubism Core、私有 IIDX 图标或此前的安装包。
默认图标由项目自己的绘图代码生成。首次从源码构建需要安装开发工具和依赖。

### macOS

需要 Python（推荐 3.13/3.14）、macOS **26.0 SDK** 和 Swift 6.2 或更新工具链。
最低运行目标为 macOS 14。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/build_icons.py
.venv/bin/python tools/build_macos.py
./start_mac.command
```

独立工具链可通过 `IIDX_MACOS_SDK` 和 `IIDX_SWIFTC` 指定。构建会将基础解析核心内置到应用。
界面语言从菜单栏 **Language / 语言** 切换。

### Windows

需要 x64 Python（推荐 3.13）、.NET 8 SDK，以及 Windows SDK / Windows 应用开发工具。
Windows ARM64 的界面可原生运行，解析进程使用 x64 Python 兼容模式。

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools\build_icons.py
dotnet build native\windows\IIDXResourceViewer.csproj -c Release -p:Platform=x64 -p:RuntimeIdentifier=win-x64
$env:IIDX_VIEWER_HOME = (Get-Location).Path
.\build\windows\IIDXResourceViewer.exe
```

ARM64 界面使用 `-p:Platform=ARM64 -p:RuntimeIdentifier=win-arm64`。
也可使用 `start_windows.bat`。界面语言从右上角切换。
单 EXE 启动器源码位于 `native/windows-launcher/`；运行环境准备及回归脚本位于 `tools/windows/`。
公开分发构建前需处理 [第三方组件的源码与许可要求](THIRD_PARTY_NOTICES.md#binary-distributions)。

## 测试与开发

```sh
.venv/bin/python -m unittest discover -s tests -v
```

真实游戏资源相关测试在缺少资源时跳过。使用 `IIDX_GAME_ROOT`、`IIDX_LIVE2D_ROOT` 指定自己的只读资源目录。
GPU 测试需要可用的图形会话。Windows 回归脚本通过 `IIDX_SHARED_PROJECT`、`IIDX_TEST_ROOT` 配置源码与临时测试目录。
修改 GLSL 后可运行 `tools/build_shaders.py`，通过 Qt Shader Tools 的 `qsb` 重建 `.qsb`。
测试素材、运行库、导出文件和个人路径不应提交到仓库。

## 许可

自有代码采用 **GPL-3.0-only**，版权所有 © 2026 AirJunk36 及贡献者，完整条款见 [LICENSE](LICENSE) 和 [COPYRIGHT](COPYRIGHT)。
第三方组件保留各自许可。本仓库未授予 Live2D 链接例外；包含专有 Cubism Core 的组合程序分发问题需单独解决。
格式参考和依赖来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
