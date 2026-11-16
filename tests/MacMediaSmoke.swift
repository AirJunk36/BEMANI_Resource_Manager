import AppKit
import AVFoundation
import Foundation

@main
struct MacMediaSmoke {
    @MainActor static func settled(_ studio: SongStudio) async throws {
        let deadline = Date().addingTimeInterval(120)
        while studio.busy {
            guard Date() < deadline else { fatalError("Song studio timeout: \(studio.status)") }
            try await Task.sleep(for:.milliseconds(25))
        }
    }
    @MainActor static func main() async throws {
        let model=ViewerModel(); defer { model.backend?.stop() }
        await model.startup()
        guard model.files.isEmpty, model.package == nil else { fatalError("启动不应加载素材") }
        let game=URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        await model.scan(game.appendingPathComponent("33_Sparkle_Shower/data").path)
        model.search="Ascalon"; await model.library.waitForResults()
        guard model.filteredFiles.contains(where: { $0.name == "20032.ifs" }),
              model.filteredFiles.contains(where: { $0.name == "19po.mp4" }) else { fatalError("曲名未关联音频与泛用 BGA") }
        print("Title search: Ascalon matches audio, Layer and 19po generic BGA (\(model.filteredFiles.count) resources)")
        let source=game.appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs")
        await model.open(source.path); await model.selectItem()
        let context=try await model.request("export_context",["animation":0])
        let worker=try BackgroundCore(home:model.home,context:context)
        try await worker.open()
        let target=model.home.appendingPathComponent("animation.mp4")
        _ = try await worker.request("export",["kind":"mp4","animation":0,"target":target.path,"resolution":"480p","fps":60])
        let duration=try await AVURLAsset(url:target).load(.duration)
        guard duration.seconds>0, duration.seconds<2 else { fatalError("MP4 无法原生读取") }
        worker.stop()
        await model.render(3)
        guard model.frame==3, model.image != nil else { fatalError("后台导出影响了主预览") }
        print("Background animation MP4: native AVFoundation playback duration \(duration.seconds)s; interactive renderer preserved")
        await model.open(game.appendingPathComponent("33_Sparkle_Shower/data/sound/20032.ifs").path)
        let job=try BackgroundCore(home:model.home,context:try await model.request("export_context"))
        try await job.open()
        let info=try await job.decoded(SongInfo.self,"song_info")
        let visuals=try await job.decoded(SongVisuals.self,"song_visuals")
        guard visuals.bga_path?.hasSuffix("19po.mp4")==true,visuals.layer_animation=="seq" else { fatalError("曲库画面对应失败") }
        let pending=Task { try await job.request("prepare_song",["bank":info.bank,"chart":1]) }
        try await Task.sleep(for:.milliseconds(500))
        let start=ProcessInfo.processInfo.systemUptime
        job.stop()
        let elapsed=ProcessInfo.processInfo.systemUptime-start
        do { _ = try await pending.value; fatalError("取消未生效") } catch { }
        guard elapsed<2, !job.backend.process.isRunning else { fatalError("取消后台处理过慢") }
        print("Cancellation: worker and encoder group stopped in \(elapsed)s")
        let studio = SongStudio(model:model)
        defer { studio.cancel() }
        studio.load(); try await settled(studio)
        guard studio.chart >= 0, !studio.videoMode else { fatalError("Audio studio failed: \(studio.status)") }
        studio.setVisualPath("bga",model.home.appendingPathComponent("missing.mp4").path)
        studio.setVisualPath("layer",model.home.appendingPathComponent("missing.ifs").path)
        studio.preview(); try await settled(studio)
        guard studio.status == "播放中", let asset = studio.player.currentItem?.asset else { fatalError("Audio preview used visual settings: \(studio.status)") }
        let videoTracks = try await asset.loadTracks(withMediaType:.video)
        guard videoTracks.isEmpty else { fatalError("Audio preview generated video") }
        studio.player.pause(); studio.videoMode = true
        studio.setVisualPath("bga",game.appendingPathComponent("33_Sparkle_Shower/data/movie/19po.mp4").path)
        studio.setVisualPath("layer",source.path); try await settled(studio)
        guard studio.layerPath == source.path, studio.layerAnimation == -2,
              studio.layerChoices.contains(where: { $0.id >= 0 }) else { fatalError("Manual Layer failed: \(studio.status)") }
        print("Independent audio preview ignores invalid visual paths; manual BGA and Layer selection passed")
    }
}
