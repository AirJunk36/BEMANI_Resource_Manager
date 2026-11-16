# Optional Live2D integration

本仓库的自有代码采用 **GPL-3.0-only**，没有额外的 Live2D 链接例外。
Cubism Core 使用专有许可，`live2d-py` 包装层的 MIT 许可不覆盖其中的 Core。

因此源码仓库不包含 Core DLL、静态库、SDK 安装包或游戏模型。
基础依赖不自动安装 Live2D 运行库。仅在自行取得并接受相应许可、用于
本地开发测试时，可安装可选依赖：

```sh
python -m pip install -r requirements-live2d.txt
```

Windows 的当前绑定还需要开发者自行提供官方 SDK 的
`Live2DCubismCore.dll`，放到 `iidxviewer/vendor/live2d/`。
该文件被 Git 忽略。macOS 的当前 wheel 将 Core 链接在原生扩展中。
构建包含可选运行库的 macOS 私有测试包时，需显式设置
`IIDX_INCLUDE_LIVE2D=1`。

以上安装步骤只描述技术依赖，不表示解决了 GPL 与专有 Core 的组合分发问题。
公开发布包含 Core 的程序前，需要处理许可兼容性，并核对 Live2D 对 SDK
再分发、编辑工具及用途的条款。项目的 GPL 许可不授予这些额外权利。

- [Cubism Core 专有许可](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)
- [Cubism Framework 许可](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)
- [保留的第三方声明](../iidxviewer/vendor/live2d/NOTICE.txt)

Without an independently resolved licensing basis, do not distribute a
combined GPL/proprietary Cubism build. The source-only publication does not
grant an exception or claim that removing the DLL from Git resolves binary
distribution compatibility. Ordinary IIDX image, animation, audio and video
features do not require the optional Live2D runtime.
