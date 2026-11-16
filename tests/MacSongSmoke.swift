import AppKit
import AVFoundation
import Foundation

@main
struct MacSongSmoke {
    @MainActor static func main() async throws {
        let model = ViewerModel()
        defer { model.backend?.stop() }
        await model.startup()
        guard model.package == nil, model.files.isEmpty else { fatalError("启动应为空") }
        let game = URL(fileURLWithPath: ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        let source = game.appendingPathComponent("20_tricoro/data/sound/20032/20032.2dx")
        let before = try Data(contentsOf: source)
        await model.open(source.path)
        guard model.canExportSong else { fatalError("整曲导出入口未启用") }
        let info = try await model.decodedRequest(SongInfo.self, "song_info")
        guard !info.needs_chart, info.charts.contains(where: { $0.id == 1 }) else { fatalError("未找到配套谱面") }
        let output = model.home.appendingPathComponent("song.wav")
        _ = try await model.request("export_song", ["bank": info.bank, "chart": 1, "target": output.path])
        let duration = try await AVURLAsset(url: output).load(.duration)
        guard duration.seconds > 110, duration.seconds < 125, try Data(contentsOf: source) == before else { fatalError("歌曲时长或源文件不符") }
        await model.open(game.appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs").path)
        await model.selectItem(); await model.render(3)
        guard model.image != nil, model.frame == 3, model.renderer?.hardware == true else { fatalError("导出后无法继续 GPU 预览") }
        print("Standalone native export: \(duration.seconds)s WAV; source unchanged; subsequent Metal preview passed")
    }
}
