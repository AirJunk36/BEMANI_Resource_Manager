import AppKit
import AVFoundation

@main
struct MacSongLayersSmoke {
    @MainActor static func settled(_ studio: SongStudio) async throws {
        let until = Date().addingTimeInterval(120)
        while studio.busy {
            guard Date() < until else { fatalError("Timeout: \(studio.status)") }
            try await Task.sleep(for:.milliseconds(20))
        }
    }
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        let model = ViewerModel()
        defer { model.mediaControls.stop(); model.backend?.stop() }
        await model.startup()
        guard model.files.isEmpty, model.package == nil else { fatalError("Startup loaded files") }
        let fixture = URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_MEDIA_FIXTURES"]!)
        let bank = fixture.appendingPathComponent("34_S3PTest/data/sound/20032.s3p")
        await model.open(bank.path); await model.selectItem()
        guard model.package?.displayTitle.contains("Ascalon · Qrispy Joybox") == true,
              EngineInfo(family:"27+",library:nil).label == "27代+/SDVX6+" else { fatalError("Package song label or engine group") }
        guard model.canExportSong, model.hasSongPreview else { fatalError("S3P full-song command unavailable") }
        model.selectedItem = "song"; await model.selectItem()
        guard model.isSongPreview, model.previewKind == "song", model.asset == nil else { fatalError("Song preview row selected an audio track") }
        let studio = SongStudio(model:model)
        guard !studio.originalAudio else { fatalError("S3P song opened as one track") }
        studio.load(preview:true); try await settled(studio)
        guard studio.canPreview, studio.chart == 1 else { fatalError("S3P chart: \(studio.status)") }
        guard studio.player.currentItem != nil, studio.player.rate == 0, !studio.useBGA, !studio.useLayer else { fatalError("Main song preview did not start in paused audio mode") }
        studio.preview(); try await settled(studio)
        guard studio.status == "播放中", let audio = studio.player.currentItem?.asset else { fatalError("S3P audio: \(studio.status)") }
        guard try await audio.load(.duration).seconds >= 1.99 else { fatalError("Late keys were truncated") }
        studio.player.pause(); studio.videoMode = true
        studio.useBGA = true
        await studio.player.seek(to:CMTime(seconds:0.5,preferredTimescale:600))
        studio.preview(video:true,autoplay:false,preservePosition:true); try await settled(studio)
        guard studio.status == "就绪", studio.player.rate == 0, abs(studio.player.currentTime().seconds - 0.5) < 0.1,
              let movie = studio.player.currentItem?.asset else { fatalError("S3P BGA or position: \(studio.status)") }
        guard try await movie.load(.duration).seconds >= 1.99,
              try await !movie.loadTracks(withMediaType:.video).isEmpty else { fatalError("S3P BGA missing") }
        studio.cancel()
        let boss=fixture.appendingPathComponent("27_BossTest/data/sound/26007.s3p")
        if FileManager.default.fileExists(atPath:boss.path) {
            await model.open(boss.path);await model.selectItem()
            let bossStudio=SongStudio(model:model);bossStudio.videoMode=true;bossStudio.load();try await settled(bossStudio)
            guard bossStudio.layerStates.count==6,bossStudio.hasLayerGauge else {fatalError("Boss states or HP controls missing")}
            bossStudio.layerState="hit";bossStudio.layerGauge=50
            bossStudio.preview(video:true,autoplay:false);try await settled(bossStudio)
            guard bossStudio.status=="就绪",bossStudio.player.currentItem != nil else {fatalError("Boss damage/HP preview")}
            bossStudio.cancel()
        }
        let voice = model.home.appendingPathComponent("voice.s3p")
        try FileManager.default.copyItem(at:bank,to:voice)
        await model.open(voice.path); await model.selectItem()
        guard !model.hasSongPreview else { fatalError("Voice bank has a full-song row") }
        let voiceStudio = SongStudio(model:model)
        guard voiceStudio.originalAudio else { fatalError("Voice bank requested a chart") }
        voiceStudio.load(); try await settled(voiceStudio)
        guard voiceStudio.canPreview else { fatalError("Voice preview: \(voiceStudio.status)") }
        voiceStudio.cancel()
        let game = URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        for song in ["03013","16020","26068","28053"] {
            await model.open(game.appendingPathComponent("33_Sparkle_Shower/data/sound/\(song).ifs").path)
            let songStudio = SongStudio(model:model); songStudio.videoMode = true
            songStudio.load(); try await settled(songStudio)
            guard songStudio.layerAnimation == -2,
                  songStudio.layerChoices.contains(where: { $0.id == -2 && $0.name == "跟随谱面" }) else { fatalError("Layer defaults: \(song), \(songStudio.status)") }
            songStudio.cancel()
        }
        await model.scan(fixture.appendingPathComponent("20_MediaTest/data").path)
        model.search="qrispy";await model.library.waitForResults()
        guard model.filteredFiles.count == 3 else { fatalError("Composer search did not find audio, Layer and BGA") }
        print("PASS: package title/composer and search; S3P full song, native audio/BGA, voice bank; automatic Layer defaults")
    }
}
