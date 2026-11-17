# Native material export · Beta2

- Images and atlases retain their source dimensions.
- A frame PNG trims transparent outer margins without resizing its pixels.
- PNG sequences, WebP, GIF and animation MP4 use one rectangle covering the animation's content and movement. Frames keep the same dimensions and origin throughout the animation.
- Standalone animation and video GIF exports use native dimensions. The preview resolution selector does not enlarge these files.
- Full-song BGA + Layer exports continue to use the selected video resolution.

Odd-sized animation MP4 files use software H.264 4:4:4 to preserve their exact width and height. Playback requires a player supporting this H.264 format. Even-sized exports retain the existing encoder path.

Legacy numeric IDX and TXP2 gameplay packages use the 304 × 416 movie window at `(168, 0)` in the 640 × 480 screen coordinate system. Fitting the entire screen into the BGA had reduced and shifted the layer. VANESSA (`1409`) is checked against both its EMPRESS IDX and Resort Anthem AFP versions; its sampled frame exports at 100 × 180.

VANESSA's authored portraits start one row below the matching movie pixels.
Full-song composition compensates by one source pixel; isolated animation
previews and native material exports retain the authored coordinates.

`osprey.bin` / `owl.bin` are manifests. Their sibling IDX/GCZ artwork, audio banks, charts and movies remain in place and are opened through the manifest. Audio-bank expansion is lazy and can export individual keysounds. Missing siblings are reported; no game files are changed.
