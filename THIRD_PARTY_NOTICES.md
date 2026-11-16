# Third-party notices

The project's original code is GPL-3.0-only. Third-party components keep
their own licenses. A dependency declaration does not relicense a dependency.

| Component | License / terms | Use |
|---|---|---|
| [ifstools](https://github.com/mon/ifstools) and [kbinxml](https://github.com/mon/kbinxml) | MIT, Copyright (c) 2018 mon | IFS, textures and binary XML |
| [Pillow](https://github.com/python-pillow/Pillow) | See Pillow's bundled license | Images and generated default icons |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause and bundled third-party notices | Audio and geometry |
| [PyCryptodome](https://github.com/Legrandin/pycryptodome) | BSD / public-domain components; see package notices | Legacy resource decoding |
| [lxml](https://github.com/lxml/lxml) | BSD and bundled library notices | Transitive XML dependency |
| [tqdm](https://github.com/tqdm/tqdm) | MPL-2.0 / MIT notices, as applicable to each file | Transitive dependency |
| [PySide6 / Shiboken6 / Qt](https://doc.qt.io/qtforpython-6/licenses.html) | LGPL-3.0 / GPL alternatives or commercial terms, according to component | Offscreen Metal / Direct3D rendering |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | BSD-2-Clause for the Python wrapper | FFmpeg process interface |
| [FFmpeg](https://ffmpeg.org/legal.html) | Depends on the actual executable; the locally tested build enables GPL | Audio decoding and video encoding |
| [Python](https://docs.python.org/3/license.html) | PSF license and bundled notices | Parsing runtime |
| [.NET / Windows App SDK](https://github.com/microsoft/WindowsAppSDK) | Microsoft component and third-party notices | Windows frontend and launcher |
| [live2d-py](https://github.com/Arkueid/live2d-py) | MIT for the wrapper; Cubism components retain separate terms | Optional Live2D integration |
| Live2D Cubism Core / Framework | Proprietary Core terms / Live2D Open Software License | Not relicensed by this project; see [Live2D notes](docs/LIVE2D.md) |

The AP2 built-in name table derives from bemaniutils and retains its
[public-domain notice](iidxviewer/third_party/bemaniutils-LICENSE.txt).
The source notices for live2d-py and Cubism are retained under
`iidxviewer/vendor/live2d/`; Core binaries are excluded.

## Format references

- [573Renderer](https://github.com/Radioo/573Renderer): AFP, BSI, GE2D and legacy format documentation/readers; reference revision `d9e8ba9625579f058f330c80d96a19856e5c5a5a`.
- [bemaniutils](https://github.com/DragonMinded/bemaniutils): AFP bytecode, TXP2/TDXT, PMAN and IIDX music databases.
- [legacy-tools](https://github.com/djhackersdev/legacy-tools): legacy EOUT layout.
- [rizu](https://github.com/semyon422/rizu): IIDX chart and audio format information.
- [iidx-ps2tools](https://github.com/SaxxonPike/iidx-ps2tools): S3P/S3V container information.
- [moc3ingbird](https://github.com/OpenL2D/moc3ingbird) and [MINT](https://github.com/InsanityLabs/MINT_Moc3_INochi_Translator): documented MOC3 field layouts. No MINT source code is bundled.

These references are not redistributed SDKs or game assets. Preserve any
upstream notice when incorporating upstream code in future changes.

## Binary distributions

This initial GitHub import contains source, not the previous private app
bundles. Before distributing builds, retain the actual dependency licenses,
provide the GPL/LGPL Corresponding Source and relinking information required
by the versions shipped, and resolve the optional Cubism compatibility issue.
A generic upstream homepage alone is not a substitute for the required
Corresponding Source of a shipped FFmpeg/Qt build.

Compiled `.qsb` shaders are generated from the adjacent project-owned GLSL
sources with `tools/build_shaders.py`. The default icon is generated from
project-owned drawing code with `tools/build_icons.py`.
