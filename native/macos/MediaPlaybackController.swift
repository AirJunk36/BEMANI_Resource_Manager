import AppKit
import AVFoundation
import MediaPlayer

@MainActor
final class MediaPlaybackController {
    enum Action { case play, pause, toggle, forward, backward, position(Double) }
    private weak var model: ViewerModel?
    private weak var studio: SongStudio?
    private var monitor: Any?
    private var targets: [(MPRemoteCommand, Any)] = []
    private var timer: Timer?
    private var seekTimer: Timer?

    static func acceptsKeyboard(_ responder: NSResponder?) -> Bool {
        !(responder is NSText || responder is NSTextField || responder is NSComboBox || responder is NSSlider || responder is NSButton)
    }
    func attach(_ model: ViewerModel) {
        guard self.model == nil else { return }
        self.model = model
        monitor = NSEvent.addLocalMonitorForEvents(matching:.keyDown) { [weak self] event in
            let handled = MainActor.assumeIsolated {
                guard let self, let model = self.model, !model.working,
                      !model.showSongExport, !model.showVideoExport, !model.showInspection,
                      event.window?.attachedSheet == nil, NSApp.modalWindow == nil,
                      event.window?.sheetParent == nil, !(event.window is NSPanel),
                      Self.acceptsKeyboard(event.window?.firstResponder),
                      event.modifierFlags.intersection([.command,.option,.control,.shift]).isEmpty else { return false }
                switch event.keyCode {
                case 49 where model.animation != nil || ["audio","video","song"].contains(model.previewKind):
                    if !event.isARepeat { self.perform(.toggle) }; return true
                case 123 where model.animation != nil: model.step(-1); return true
                case 124 where model.animation != nil: model.step(1); return true
                default: return false
                }
            }
            return handled ? nil : event
        }
        let center = MPRemoteCommandCenter.shared()
        bind(center.playCommand,.play); bind(center.pauseCommand,.pause); bind(center.togglePlayPauseCommand,.toggle)
        bind(center.nextTrackCommand,.forward); bind(center.previousTrackCommand,.backward)
        center.skipForwardCommand.preferredIntervals = [5]; center.skipBackwardCommand.preferredIntervals = [5]
        bind(center.skipForwardCommand,.forward); bind(center.skipBackwardCommand,.backward)
        bindSeek(center.seekForwardCommand,.forward); bindSeek(center.seekBackwardCommand,.backward)
        let command = center.changePlaybackPositionCommand
        let target = command.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            Task { @MainActor in self?.perform(.position(event.positionTime)) }; return .success
        }
        targets.append((command,target)); refresh()
    }
    private func bind(_ command: MPRemoteCommand, _ action: Action) {
        let target = command.addTarget { [weak self] _ in
            Task { @MainActor in self?.perform(action) }; return .success
        }
        targets.append((command,target))
    }
    private func bindSeek(_ command: MPRemoteCommand, _ action: Action) {
        let target = command.addTarget { [weak self] event in
            guard let event = event as? MPSeekCommandEvent else { return .commandFailed }
            let begin = event.type == .beginSeeking
            Task { @MainActor in
                guard let self else { return }
                self.seekTimer?.invalidate(); self.seekTimer = nil
                if begin {
                    self.perform(action)
                    let timer = Timer(timeInterval:0.25,repeats:true) { [weak self] _ in Task { @MainActor in self?.perform(action) } }
                    RunLoop.main.add(timer,forMode:.common); self.seekTimer = timer
                }
            }
            return .success
        }
        targets.append((command,target))
    }
    func use(_ studio: SongStudio?) { seekTimer?.invalidate(); seekTimer = nil; self.studio = studio; refresh() }
    func stop() {
        timer?.invalidate(); timer = nil
        seekTimer?.invalidate(); seekTimer = nil
        if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil
        for (command,target) in targets { command.removeTarget(target); command.isEnabled = false }; targets.removeAll()
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        MPNowPlayingInfoCenter.default().playbackState = .stopped
        studio = nil; model = nil
    }
    private var activePlayer: AVPlayer? {
        if let studio { return studio.player.currentItem == nil ? nil : studio.player }
        guard let model, ["audio","video"].contains(model.previewKind), model.player.currentItem != nil else { return nil }
        return model.player
    }
    func perform(_ action: Action) {
        guard let model else { return }
        if let player = activePlayer {
            let duration = player.currentItem?.duration.seconds ?? 0
            func seek(_ value: Double) {
                guard duration.isFinite, duration > 0, value.isFinite else { return }
                player.seek(to:CMTime(seconds:max(0,min(duration,value)),preferredTimescale:600))
            }
            switch action {
            case .play: if studio != nil { player.play() } else if !model.isPlaying || player.timeControlStatus == .paused { model.isPlaying = false; model.togglePlay() }
            case .pause: if studio != nil { player.pause() } else { model.pause() }
            case .toggle: if studio != nil { player.rate == 0 ? player.play() : player.pause() } else { model.togglePlay() }
            case .forward: seek(player.currentTime().seconds + 5)
            case .backward: seek(player.currentTime().seconds - 5)
            case .position(let value): seek(value)
            }
        } else if studio == nil, let animation = model.animation {
            switch action {
            case .play: if !model.isPlaying { model.togglePlay() }
            case .pause: model.pause()
            case .toggle: model.togglePlay()
            case .forward: model.step(1)
            case .backward: model.step(-1)
            case .position(let seconds): model.seekFrame(max(0,min(Double(animation.length-1),seconds * animation.fps)))
            }
        }
        refresh()
    }
    func refresh() {
        guard let model else { return }
        let player = activePlayer
        let animation = studio == nil ? model.animation : nil
        let available = player != nil || animation != nil
        for (command,_) in targets { command.isEnabled = available }
        let center = MPNowPlayingInfoCenter.default()
        guard available else {
            center.nowPlayingInfo = nil; center.playbackState = .stopped
            seekTimer?.invalidate(); seekTimer = nil
            timer?.invalidate(); timer = nil; return
        }
        let duration = player?.currentItem?.duration.seconds ?? animation.map { Double($0.length) / max(1,$0.fps) } ?? 0
        let elapsed = player?.currentTime().seconds ?? (model.timelineFrame / max(1,animation?.fps ?? 1))
        let rate = player.map { Double($0.rate) } ?? (model.isPlaying ? model.speed : 0)
        center.nowPlayingInfo = [MPMediaItemPropertyTitle:studio?.title ?? model.displayTitle,
            MPNowPlayingInfoPropertyMediaType:(animation != nil || model.previewKind == "video" || studio?.videoMode == true ? MPNowPlayingInfoMediaType.video : .audio).rawValue,
            MPMediaItemPropertyPlaybackDuration:duration.isFinite ? max(0,duration) : 0,
            MPNowPlayingInfoPropertyElapsedPlaybackTime:elapsed.isFinite ? max(0,elapsed) : 0,
            MPNowPlayingInfoPropertyPlaybackRate:rate]
        center.playbackState = rate == 0 ? .paused : .playing
        if timer == nil {
            let timer = Timer(timeInterval:1,repeats:true) { [weak self] _ in Task { @MainActor in self?.refresh() } }
            RunLoop.main.add(timer,forMode:.common); self.timer = timer
        }
    }
}
