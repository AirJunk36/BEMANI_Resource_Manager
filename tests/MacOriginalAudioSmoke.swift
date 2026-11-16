import AppKit
import AVFoundation
import Foundation

@main
struct MacOriginalAudioSmoke {
    @MainActor static func main() async throws {
        let model = ViewerModel()
        defer { model.backend?.stop() }
        await model.startup()
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        for (i, relative) in [
            "33_Sparkle_Shower/data/sound/stamp/miku/miku05-2.s3v",
            "33_Sparkle_Shower/data/sound/voice/vo034_miku/vo034_06_tutorial.s3p"
        ].enumerated() {
            await model.open(root.appendingPathComponent(relative).path)
            await model.selectItem()
            guard let selected = model.asset, model.previewKind == "audio",
                  let playback = model.player.currentItem?.asset else { fatalError("音频预览失败: \(model.status)") }
            let duration = try await playback.load(.duration)
            guard duration.seconds > 0 else { fatalError("音频不可播放") }
            let info = try await model.request("audio_export_info", ["asset": selected.id])
            guard let name = info["name"] as? String, name.hasSuffix(".wma") else { fatalError("原格式识别错误") }
            let target = model.home.appendingPathComponent("original-\(i).wma")
            _ = try await model.request("export", ["kind": "audio_original", "asset": selected.id, "target": target.path])
            let raw = model.home.appendingPathComponent("encoded-\(i).bin")
            _ = try await model.request("export", ["kind": "asset", "asset": selected.id, "target": raw.path])
            guard try Data(contentsOf: target) == Data(contentsOf: raw) else { fatalError("原音频被转码") }
            print("PASS \(relative): native PCM preview \(duration.seconds)s; original WMA export byte-identical")
        }
    }
}
