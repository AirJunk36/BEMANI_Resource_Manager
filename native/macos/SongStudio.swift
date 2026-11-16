import AppKit
import AVFoundation
import SwiftUI
import UniformTypeIdentifiers

struct SongVisuals: Decodable {
    let title: String
    let bga_path: String?
    let layer_path: String?
    let bga_offset: Double
    let layer_animation: String
}
struct LayerChoice: Decodable, Identifiable { let id: Int; let name: String }
struct LayerState: Decodable, Identifiable { let id: String; let name: String }
struct LayerChoices: Decodable { let animations: [LayerChoice]; let selected: Int; let states: [LayerState]?;let gauge: Bool? }
struct RelatedSong: Decodable, Identifiable { let id: String; let name: String; let path: String; let working_path: String? }
struct RelatedSongs: Decodable { let songs: [RelatedSong] }

@MainActor
final class SongStudio: ObservableObject {
    let model: ViewerModel
    let player = AVPlayer()
    let originalAsset: AssetItem?
    @Published var originalAudio = false
    @Published var originalName = ""
    var canPreview: Bool { originalAudio ? originalAsset != nil && !originalName.isEmpty : chart >= 0 }
    @Published var info: SongInfo?
    @Published var bank = ""
    @Published var chart = -1
    @Published var chartPath: String?
    @Published var bgaPath = ""
    @Published var layerPath = ""
    @Published var useBGA = false
    @Published var useLayer = false
    @Published var bgaOffset = 0.0
    @Published var layerOffset = 0.0
    @Published var bgaLoop = true
    @Published var layerLoop = false
    @Published var blackKey = false
    @Published var layerChoices: [LayerChoice] = []
    @Published var layerAnimation = -1
    @Published var layerStates: [LayerState] = []
    @Published var layerState = "clear"
    @Published var layerGauge = 100.0
    @Published var hasLayerGauge = false
    @Published var resolution = "720p"
    @Published var fps = 60
    @Published var videoMode = false
    @Published var busy = false
    @Published var status = ""
    @Published var title = "乐曲"
    @Published var songs: [RelatedSong] = []
    @Published var songPath = ""
    private var loadedSongPath = ""
    private var loadedLayerPath = ""
    private var preferredLayer = ""
    private var core: BackgroundCore?
    private var task: Task<Void,Never>?

    init(model: ViewerModel) {
        self.model = model
        originalAsset = model.asset?.kind == "音频" ? model.asset : nil
        let source=URL(fileURLWithPath:model.package?.path ?? "")
        let voiceBank=source.pathExtension.lowercased()=="s3p" && !FileManager.default.fileExists(atPath:source.deletingPathExtension().appendingPathExtension("1").path)
        originalAudio = originalAsset != nil && (!model.canExportSong || voiceBank)
    }
    func changeAudioSource() { cancel(); videoMode = false; load() }
    func cancel() {
        task?.cancel(); player.pause(); player.replaceCurrentItem(with:nil)
        core?.stop(); core = nil; busy = false; status = "已取消"
        model.mediaControls.refresh()
    }
    private func connect() async throws -> BackgroundCore {
        if let core { return core }
        let context = try await model.request("export_context")
        try Task.checkCancellation()
        let result = try BackgroundCore(home:model.home,context:context); core = result
        try await result.open()
        if !songPath.isEmpty {
            try await result.openSong(path:songPath,workingPath:songs.first(where: { $0.path == songPath })?.working_path)
        }
        return result
    }
    private func run(_ message: String, operation: @escaping @MainActor () async throws -> Void) {
        guard !busy else { return }
        busy = true; status = message
        task = Task {
            do { try await operation() }
            catch {
                if !Task.isCancelled {
                    status = error.localizedDescription
                    if core?.backend.process.isRunning == false { core?.stop(); core = nil }
                }
            }
            if !Task.isCancelled { busy = false }
        }
    }
    private func arguments() -> [String:Any] {
        var args: [String:Any] = ["chart":chart]
        if !bank.isEmpty { args["bank"] = bank }
        if let chartPath { args["chart_path"] = chartPath }
        return args
    }
    private func options() -> [String:Any] {
        var result: [String:Any] = ["resolution":resolution,"fps":fps,"renderer":model.renderMode,
            "bga_offset":bgaOffset,"layer_offset":layerOffset,"bga_loop":bgaLoop,"layer_loop":layerLoop,"layer_black_key":blackKey,
            "layer_animation":layerAnimation,"layer_state":layerState,"layer_gauge":layerGauge]
        if useBGA && !bgaPath.isEmpty { result["bga_path"] = bgaPath }
        if useLayer && !layerPath.isEmpty { result["layer_path"] = layerPath }
        return result
    }
    private func updateInfo(_ worker: BackgroundCore) async throws {
        let result = try await worker.decoded(SongInfo.self,"song_info",arguments())
        info = result; bank = result.bank
        if !result.charts.contains(where: { $0.id == chart }) {
            chart = result.charts.first(where: { $0.id == 1 })?.id ?? result.charts.first?.id ?? -1
        }
    }
    private func updateLayer(_ worker: BackgroundCore, preferred: String = "") async throws {
        guard !layerPath.isEmpty else { layerChoices = []; layerAnimation = -1; return }
        let result = try await worker.decoded(LayerChoices.self,"layer_info",["path":layerPath,"preferred":preferred])
        layerChoices = result.animations; layerAnimation = result.selected; loadedLayerPath = layerPath
        layerStates=result.states ?? [];hasLayerGauge=result.gauge ?? false
        if !layerStates.contains(where:{$0.id==layerState}) { layerState=layerStates.first?.id ?? "clear" }
    }
    func load(preview: Bool = false) {
        if originalAudio, let originalAsset {
            run("正在读取音频…") { [self] in
                let result = try await model.request("audio_export_info", ["asset": originalAsset.id])
                originalName = result["name"] as? String ?? ""
                if preview {
                    let result = try await model.decodedRequest(PreviewInfo.self,"asset",["asset":originalAsset.id])
                    if let path = result.path { player.replaceCurrentItem(with:AVPlayerItem(url:URL(fileURLWithPath:path))) }
                }
                title = originalAsset.name; status = "就绪"
                model.mediaControls.refresh()
            }
            return
        }
        run("正在读取乐曲…") { [self] in
            let worker = try await connect()
            if !model.canExportSong {
                songs = try await worker.decoded(RelatedSongs.self,"related_songs").songs
                guard let first = songs.first else { status = "未找到对应乐曲，请选择音频包"; return }
                songPath = first.path
                try await worker.openSong(path:first.path,workingPath:first.working_path); loadedSongPath = first.path
            }
            try await loadVisuals(worker)
            if preview {
                useBGA = false; useLayer = false
                if chart >= 0 {
                    let result = try await worker.decoded(PreviewInfo.self,"prepare_song",arguments())
                    guard let path = result.path else { throw BackendError(message:"预览未生成") }
                    player.replaceCurrentItem(with:AVPlayerItem(url:URL(fileURLWithPath:path)))
                    model.mediaControls.refresh()
                }
            }
        }
    }
    private func loadVisuals(_ worker: BackgroundCore) async throws {
            try await updateInfo(worker)
            let visuals = try await worker.decoded(SongVisuals.self,"song_visuals")
            title = visuals.title.isEmpty ? "乐曲" : visuals.title
            bgaPath = visuals.bga_path ?? ""; layerPath = visuals.layer_path ?? ""
            useBGA = !bgaPath.isEmpty; useLayer = !layerPath.isEmpty; bgaOffset = visuals.bga_offset
            bgaLoop = !URL(fileURLWithPath:bgaPath).deletingPathExtension().lastPathComponent.allSatisfy(\.isNumber)
            preferredLayer = visuals.layer_animation; loadedLayerPath = ""; layerChoices = []; layerAnimation = -1; layerOffset = 0
            if videoMode { try await updateLayer(worker,preferred:preferredLayer) }
            status = "就绪"
    }
    func songChanged() {
        guard !songPath.isEmpty, songPath != loadedSongPath else { return }
        player.pause(); player.replaceCurrentItem(with:nil)
        run("正在读取乐曲…") { [self] in
            let worker = try await connect()
            try await worker.openSong(path:songPath,workingPath:songs.first(where: { $0.path == songPath })?.working_path)
            loadedSongPath = songPath; bank = ""; chart = -1
            try await loadVisuals(worker)
        }
    }
    func chooseSong() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.title = tr("选择歌曲音频包")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if !songs.isEmpty && !songs.contains(where: { $0.path == url.path }) {
            songs.append(RelatedSong(id:url.path,name:url.lastPathComponent,path:url.path,working_path:nil))
        }
        songPath = url.path; songChanged()
    }
    func refresh() {
        run("正在读取谱面…") { [self] in let worker = try await connect(); try await updateInfo(worker); status = "就绪" }
    }
    func chartChanged() {
        if let recommended = info?.charts.first(where: { $0.id == chart })?.bank, recommended != bank { bank = recommended }
    }
    func choose(_ kind: String) {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false
        panel.title = tr(kind == "chart" ? "选择配套 .1 谱面" : kind == "bga" ? "选择 BGA" : "选择 Layer")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if kind == "chart" { chartPath = url.path; refresh() }
        else { setVisualPath(kind,url.path) }
    }
    func setVisualPath(_ kind: String, _ path: String) {
        let path = (path.trimmingCharacters(in:.whitespacesAndNewlines) as NSString).expandingTildeInPath
        if kind == "bga" { bgaPath = path; useBGA = !path.isEmpty }
        else { layerPath = path; useLayer = !path.isEmpty; preferredLayer = ""; loadedLayerPath = ""; layerChoices = []; layerAnimation = -1; prepareLayer() }
    }
    func prepareLayer() {
        guard videoMode, useLayer, !layerPath.isEmpty, loadedLayerPath != layerPath else { return }
        run("正在读取 Layer…") { [self] in let worker = try await connect(); try await updateLayer(worker,preferred:preferredLayer); status = "就绪" }
    }
    private func videoArguments(_ worker: BackgroundCore) async throws -> [String:Any] {
        bgaPath = (bgaPath.trimmingCharacters(in:.whitespacesAndNewlines) as NSString).expandingTildeInPath
        layerPath = (layerPath.trimmingCharacters(in:.whitespacesAndNewlines) as NSString).expandingTildeInPath
        if useLayer && !layerPath.isEmpty && (loadedLayerPath != layerPath || layerAnimation == -1) {
            try await updateLayer(worker,preferred:preferredLayer)
        }
        return options()
    }
    func preview(video: Bool = false, autoplay: Bool = true, preservePosition: Bool = false) {
        let position = preservePosition ? player.currentTime().seconds : 0
        player.pause()
        run("正在准备预览…") { [self] in
            let result: PreviewInfo
            if originalAudio, let originalAsset {
                result = try await model.decodedRequest(PreviewInfo.self,"asset",["asset":originalAsset.id])
            } else {
                let worker = try await connect(); var args = arguments()
                if video { var options = try await videoArguments(worker);options["preview"]=true;args["options"] = options }
                result = try await worker.decoded(PreviewInfo.self,video ? "prepare_song_movie" : "prepare_song",args)
            }
            guard let path = result.path else { throw BackendError(message:"预览未生成") }
            player.replaceCurrentItem(with:AVPlayerItem(url:URL(fileURLWithPath:path)))
            if position.isFinite && position > 0 { await player.seek(to:CMTime(seconds:position,preferredTimescale:600)) }
            if autoplay { player.play() }
            status = autoplay ? "播放中" : "就绪"
            model.mediaControls.refresh()
        }
    }
    func export(video: Bool, gif: Bool = false) {
        if originalAudio, let originalAsset {
            let panel = NSSavePanel(); panel.title = tr("导出原音频"); panel.canCreateDirectories = true
            panel.nameFieldStringValue = originalName
            guard panel.runModal() == .OK, let target = panel.url else { return }
            exportOriginal(to:target,asset:originalAsset)
            return
        }
        let panel = NSSavePanel(); panel.title = tr(gif ? "导出乐曲 GIF" : video ? "导出乐曲 MP4" : "导出整曲 WAV")
        panel.allowedContentTypes = gif ? [.gif] : video ? [.mpeg4Movie] : [.wav]; panel.canCreateDirectories = true
        let base = !songPath.isEmpty ? URL(fileURLWithPath:songPath).deletingPathExtension().lastPathComponent : model.package.map { URL(fileURLWithPath:$0.path).deletingPathExtension().lastPathComponent } ?? "song"
        let name = info?.charts.first(where: { $0.id == chart })?.name.replacingOccurrences(of:" ",with:"_") ?? ""
        let replaced=core?.context["modified"] as? Bool ?? model.modified
        panel.nameFieldStringValue = "\(exportName(base+"_"+name,fallback:"song",replaced:replaced)).\(gif ? "gif" : video ? "mp4" : "wav")"
        guard panel.runModal() == .OK, let target = panel.url else { return }
        player.pause()
        run(gif ? "正在导出 GIF…" : video ? "正在合成 MP4…" : "正在合成歌曲…") { [self] in
            let worker = try await connect(); var args = arguments(); args["target"] = target.path
            if video { args["options"] = try await videoArguments(worker) }
            _ = try await worker.request(gif ? "export_song_gif" : video ? "export_song_movie" : "export_song",args)
            status = "已导出：\(target.path)"; model.status = status
        }
    }
    func exportOriginal(to target: URL, asset: AssetItem? = nil) {
        guard let selected = asset ?? originalAsset else { return }
        run("正在导出…") { [self] in
            _ = try await model.request("export",["kind":"audio_original","asset":selected.id,"target":target.path])
            status = "已导出：\(target.path)"; model.status = status
        }
    }
}
