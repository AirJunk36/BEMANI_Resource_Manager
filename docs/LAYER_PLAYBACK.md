# Layer playback

- **Clear**（默认）：谱面判定均为 PG。
- **Failed**：谱面判定均为 POOR，不再沿用 Clear 的前半段。
- 受击、攻击和失误特效按对应控制器触发；不重启仍在播放的特效。
- 谱面结束时立即切换结尾，打断尚未结束的长片段。BOSS 使用 Clear / Failed 对应结尾；普通 Layer 使用包内的 `out` / `end` 等片段，没有结尾组件时停止显示。
- 预览与 MP4 / GIF 导出共用播放逻辑，手动选择的动画也遵守谱面结束时间。

血条与攻击组件按实际素材包识别。旧版 ICARUS 使用原有 HP 贴图，横向覆盖预览画面，数字随选定谱面判定更新。新版包缺少 HP / Military 失误组件时不补绘或跨版本借用。Rche 等命名控制器的判定特效独立于血条，组件存在时继续播放。

本机相关回归包含 16、19、33 代；未取得 34 代本地素材，尚未实测该版本。血量按判定进度模拟，尚未完整还原所有 BOSS 的实际伤害及难度公式。

Generated export names use English/ASCII labels. Replacement copy titles append `_replaced` once. Internal identifiers and filenames remain intact where encryption or related files depend on them.

Clear uses all-PG judgements; Failed uses all-POOR judgements. Chart end interrupts long clips and selects the matching ending. Components are used only when supplied by the actual resource package; absent modern HP artwork is never replaced with a synthetic overlay. The same timeline drives preview and video/GIF export.
