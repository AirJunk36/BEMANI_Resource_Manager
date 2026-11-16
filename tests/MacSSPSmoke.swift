import AppKit
import AVFoundation
import Foundation

@main
struct MacSSPSmoke {
    @MainActor static func main() async throws {
        let model = ViewerModel()
        defer { model.backend?.stop() }
        await model.startup()
        guard model.package == nil, model.files.isEmpty else { fatalError("启动不应加载资源") }
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        for (relative, count) in [
            ("16_EMPRESS/data/sd_data/sys_sd/sys_sound.ssp", 60),
            ("18_Resort_Anthem/data/sd_data/sys_sd/sys_sound.ssp", 325),
            ("19_Lincle_cn/contents/modules/data/sound/sys_sd/sys_sound.ssp", 293)
        ] {
            await model.open(root.appendingPathComponent(relative).path)
            guard model.package?.assets.count == count else { fatalError("SSP 读取失败: \(model.status)") }
            await model.selectItem()
            guard model.previewKind == "audio", let asset = model.player.currentItem?.asset else {
                fatalError("SSP 音频预览失败: \(model.status)")
            }
            let duration = try await asset.load(.duration)
            let tracks = try await asset.loadTracks(withMediaType: .audio)
            guard duration.seconds > 0, tracks.count == 1 else { fatalError("原生音频读取失败") }
            let target = model.home.appendingPathComponent("unpacked-\(count)")
            _ = try await model.request("export", ["kind": "audio", "target": target.path])
            let files = try FileManager.default.contentsOfDirectory(at: target, includingPropertiesForKeys: nil)
            guard files.filter({ $0.pathExtension == "wav" }).count == count,
                  files.contains(where: { $0.lastPathComponent == "index.json" }) else { fatalError("SSP 解包不完整") }
            print("PASS \(relative): \(count) tracks, AVFoundation \(duration.seconds)s, complete export")
        }
    }
}
