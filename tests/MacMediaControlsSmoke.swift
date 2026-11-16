import AppKit
import AVFoundation
import MediaPlayer

@main
struct MacMediaControlsSmoke {
    @MainActor static func settled(_ studio: SongStudio) async throws {
        let end = Date().addingTimeInterval(60)
        while studio.busy {
            guard Date() < end else { fatalError("Studio timeout: \(studio.status)") }
            try await Task.sleep(for:.milliseconds(20))
        }
    }
    @MainActor static func wait(_ message: String, until ready: () -> Bool) async throws {
        let end = Date().addingTimeInterval(5)
        while !ready() {
            guard Date() < end else { fatalError(message) }
            try await Task.sleep(for:.milliseconds(25))
        }
    }
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        let inputs: [NSResponder] = [NSTextView(), NSTextField(), NSSearchField(), NSComboBox(), NSSlider(), NSButton()]
        for responder in inputs {
            guard !MediaPlaybackController.acceptsKeyboard(responder) else { fatalError("Input intercepted by playback") }
        }
        guard MediaPlaybackController.acceptsKeyboard(NSView()) else { fatalError("Preview keyboard disabled") }
        let model = ViewerModel()
        defer { model.mediaControls.stop(); model.backend?.stop() }
        model.mediaControls.attach(model)
        await model.startup()
        let root = URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        let source = root.appendingPathComponent("33_Sparkle_Shower/data/sound/stamp/miku/miku05-2.s3v")
        await model.open(source.path); await model.selectItem()
        guard model.canOpenSongStudio else { fatalError("S3V audio studio unavailable") }
        _ = try await model.player.currentItem!.asset.load(.duration)
        model.mediaControls.refresh()
        guard MPRemoteCommandCenter.shared().togglePlayPauseCommand.isEnabled else { fatalError("System media commands disabled") }
        model.mediaControls.perform(.play)
        try await wait("System play failed: \(model.status), playing=\(model.isPlaying), rate=\(model.player.rate), state=\(model.player.timeControlStatus.rawValue), item=\(model.player.currentItem?.status.rawValue ?? -1)") { model.isPlaying && model.player.currentTime().seconds > 0 }
        model.mediaControls.perform(.pause)
        guard !model.isPlaying else { fatalError("System pause failed") }
        model.mediaControls.perform(.forward)
        try await wait("System forward failed") { model.player.currentTime().seconds > 4 }
        model.mediaControls.perform(.backward)
        try await wait("System backward failed") { model.player.currentTime().seconds < 0.2 }
        let studio = SongStudio(model:model)
        defer { studio.cancel() }
        studio.load(); try await settled(studio)
        guard studio.originalAudio, studio.canPreview, studio.originalName == "miku05-2.wma" else { fatalError("Original audio mode failed: \(studio.status)") }
        model.mediaControls.use(studio)
        studio.preview(); try await settled(studio)
        model.mediaControls.perform(.pause)
        guard studio.player.rate == 0 else { fatalError("Studio system pause failed") }
        model.mediaControls.perform(.play)
        try await Task.sleep(for:.milliseconds(100))
        guard studio.player.rate > 0, !model.isPlaying else { fatalError("System control targeted the wrong player") }
        let output = model.home.appendingPathComponent("studio-original.wma")
        studio.exportOriginal(to:output); try await settled(studio)
        guard try Data(contentsOf:output) == Data(contentsOf:source) else { fatalError("Studio transcoded original audio") }
        model.mediaControls.use(nil); studio.cancel()
        await model.open(root.appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs").path); await model.selectItem()
        model.mediaControls.perform(.forward)
        guard model.timelineFrame == 1 else { fatalError("Animation forward failed") }
        model.mediaControls.perform(.backward)
        guard model.timelineFrame == 0 else { fatalError("Animation backward failed") }
        print("PASS: text input shortcut policy; system play/pause/seek; studio player handoff; original WMA export; animation frame controls")
    }
}
