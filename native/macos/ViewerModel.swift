import AppKit
import AVFoundation
import Combine
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum PreviewScale: String, CaseIterable {
    case actual = "1:1", widescreen = "16:9", standard = "4:3", fit = "适应", stretch = "拉伸"

    func size(_ source: NSSize, in viewport: NSSize) -> NSSize {
        let w = max(1, source.width), h = max(1, source.height)
        let available = NSSize(width: max(1, viewport.width), height: max(1, viewport.height))
        if self == .actual { return NSSize(width: w, height: h) }
        if self == .stretch { return available }
        let ratio = self == .widescreen ? 16.0 / 9 : self == .standard ? 4.0 / 3 : w / h
        let width = min(available.width, available.height * ratio)
        return NSSize(width: width, height: width / ratio)
    }
}

enum PreviewGeometry {
    static func normalized(_ point: CGPoint, viewport: CGSize, image: CGSize, zoom: Double, offset: CGSize) -> CGPoint {
        let width = max(1, image.width * zoom), height = max(1, image.height * zoom)
        return CGPoint(x: (point.x - (viewport.width - width) / 2 - offset.width) / width,
                       y: (point.y - (viewport.height - height) / 2 - offset.height) / height)
    }
}

@MainActor
final class PreviewState: ObservableObject {
    @Published var image: NSImage?
    @Published var timelineFrame = 0.0
    @Published var mediaTime = 0.0
    @Published var mediaDuration = 0.0
}

@MainActor
final class ViewerModel: ObservableObject {
    static let animationPlaybackFPS = 60.0
    static func isSongBank(_ path: String) -> Bool { ["2dx","s3p"].contains(URL(fileURLWithPath:path).pathExtension.lowercased()) }
    let library = ResourceCatalog()
    let mediaControls = MediaPlaybackController()
    @Published var files: [ResourceFile] = [] { didSet { library.replace(files) } }
    @Published var root = ""
    var search: String { get { library.query } set { library.query = newValue } }
    var version: String { get { library.version } set { library.version = newValue } }
    var kind: String { get { library.kind } set { library.kind = newValue } }
    @Published var assetSearch = ""
    @Published var selectedFile: String?
    @Published var selectedItem: String?
    @Published var package: PackageInfo? {
        didSet {
            songAvailable = package.map { Self.isSongBank($0.path) || $0.assets.contains { $0.kind == "音频包" && Self.isSongBank($0.name) } } ?? false
            animationsByID = Dictionary(uniqueKeysWithValues: (package?.animations ?? []).map { ($0.id, $0) })
            assetsByID = Dictionary(uniqueKeysWithValues: (package?.assets ?? []).map { ($0.id, $0) })
            contentQuery = nil
        }
    }
    let preview = PreviewState()
    var image: NSImage? { get { preview.image } set { preview.image = newValue } }
    @Published var previewKind = "empty" { didSet { updateClock() } }
    @Published var text = ""
    @Published var title = "选择素材"
    @Published var showLayers = false
    @Published var selectedLayerID: String?
    @Published var selectedLayerBounds: CGRect?
    private var pickToken: Int?
    private var pickingLayer: Bool { pickToken == generation }
    @Published var status = "就绪"
    @Published var warnings: [String] = []
    @Published var working = false
    var frame: Double = 0
    var timelineFrame: Double { get { preview.timelineFrame } set { preview.timelineFrame = newValue } }
    @Published var isPlaying = false { didSet { updateClock(); mediaControls.refresh() } }
    @Published var loop = true
    @Published var speed = 1.0
    @Published var scaleMode: PreviewScale = .fit
    @Published var background = "棋盘"
    @Published var previewResolution = "1080p"
    @Published var playbackFPS = ViewerModel.animationPlaybackFPS { didSet { updateClock() } }
    @Published var renderMode = "gpu"
    var mediaTime: Double { get { preview.mediaTime } set { preview.mediaTime = newValue } }
    var mediaDuration: Double { get { preview.mediaDuration } set { preview.mediaDuration = newValue } }
    let player = AVPlayer()
    let home: URL
    var backend: Backend?
    var renderBusy = false
    var pendingFrame: Int?
    var generation = 0
    var openGeneration = 0
    var baseFrame = 0
    var startTime = 0.0
    let rawFrames = RawFrameReader()
    @Published var renderer: RendererInfo?
    @Published var inspectionText = ""
    @Published var showInspection = false
    @Published var showLiveEditor = false
    @Published var showSongExport = false
    @Published var showVideoExport = false
    var videoJob: VideoExportJob?
    private var songAvailable = false
    var canExportSong: Bool { songAvailable && !working }
    var hasSongPreview: Bool { package?.song_preview == true }
    var displayTitle: String { modified ? exportName(title,fallback:"asset",replaced:true) : isSongPreview || previewKind == "empty" ? tr(title) : title }
    var isSongPreview: Bool { selectedItem == "song" }
    var canOpenSongStudio: Bool {
        guard !working, let package else { return false }
        if songAvailable || package.assets.contains(where: { $0.kind == "音频" }) { return true }
        let path = URL(fileURLWithPath:package.path)
        return ["movie","graphic","graph"].contains(path.deletingLastPathComponent().lastPathComponent.lowercased())
    }
    private var animationsByID: [Int: AnimationItem] = [:]
    private var assetsByID: [Int: AssetItem] = [:]
    private var contentQuery: String?
    private var animationMatches: [AnimationItem] = []
    private var assetMatches: [String: [AssetItem]] = [:]
    private var clock: AnyCancellable?
    private var clockInterval: TimeInterval?
    private var lastTimelineUpdate = 0.0

    init() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--home"), i + 1 < args.count {
            home = URL(fileURLWithPath: args[i + 1])
        } else {
            home = Bundle.main.bundleURL.deletingLastPathComponent()
        }
        do { backend = try Backend(home: home) }
        catch { status = error.localizedDescription; warnings = [error.localizedDescription] }
    }

    var filteredFiles: [ResourceFile] { library.results.files }
    var versions: [String] { library.versions }
    private func updateContentMatches() {
        if contentQuery == assetSearch { return }
        animationMatches = package?.animations.filter { matching($0.name) } ?? []
        assetMatches = Dictionary(grouping: package?.assets.filter { matching($0.name) } ?? [], by: \.kind)
        contentQuery = assetSearch
    }
    var filteredAnimations: [AnimationItem] { updateContentMatches(); return animationMatches }
    func assets(in group: String) -> [AssetItem] { updateContentMatches(); return assetMatches[group] ?? [] }
    var animation: AnimationItem? {
        guard let selection = selectedItem, selection.hasPrefix("a:"), let id = Int(selection.dropFirst(2)) else { return nil }
        return animationsByID[id]
    }
    var asset: AssetItem? {
        guard let selection = selectedItem, selection.hasPrefix("f:"), let id = Int(selection.dropFirst(2)) else { return nil }
        return assetsByID[id]
    }
    var groups: [String] { ["图片", "图集", "音频", "音频包", "视频", "文件"] }
    var canReplaceAsset: Bool { asset?.replaceable == true && !working }
    var modified: Bool { package?.modified == true && package?.format != "live2d" }
    var canUndoReplacement: Bool { package?.can_undo == true && !working && package?.format != "live2d" }
    var draftLabel: String { package?.modified == true ? "副本 · \(package?.change_count ?? 0) 次修改" : "" }
    func matching(_ name: String) -> Bool { assetSearch.isEmpty || name.localizedCaseInsensitiveContains(assetSearch) }

    func request(_ method: String, _ args: [String: Any] = [:]) async throws -> [String: Any] {
        guard let backend else { throw BackendError(message: "解析核心不可用，请重新打开应用。") }
        return try await backend.request(method, args)
    }

    func decodedRequest<T: Decodable>(_ type: T.Type, _ method: String, _ args: [String: Any] = [:]) async throws -> T {
        guard let backend else { throw BackendError(message: "解析核心未启动") }
        return try await backend.decoded(type, method, args)
    }

    private func updateClock() {
        let interval: TimeInterval? = isPlaying && animation != nil ? 1 / playbackFPS :
            ["audio", "video"].contains(previewKind) ? (isPlaying ? 1.0 / 30 : 1.0 / 10) : nil
        guard interval != clockInterval else { return }
        clock?.cancel(); clock = nil; clockInterval = interval
        if let interval {
            clock = Timer.publish(every: interval, on: .main, in: .common).autoconnect().sink { [weak self] _ in self?.tick() }
        }
    }

    func startup() async {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--open"), i + 1 < args.count {
            root = URL(fileURLWithPath: args[i + 1]).deletingLastPathComponent().path
            await open(args[i + 1])
        } else if let i = args.firstIndex(of: "--root"), i + 1 < args.count {
            await scan(args[i + 1])
        }
    }

    func scan(_ path: String) async {
        pause(); working = true; status = "正在扫描…"; root = path
        defer { working = false }
        do {
            let reply = try await decodedRequest(ScanResult.self, "scan", ["root": path])
            files = reply.files; root = reply.root
            status = "找到 \(files.count.formatted()) 个文件"
        } catch { fail(error) }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.message = tr("选择游戏目录或版本总目录")
        if panel.runModal() == .OK, let url = panel.url { Task { await scan(url.path) } }
    }
    func chooseFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false; panel.canChooseFiles = true
        panel.allowedContentTypes = [.data]
        if panel.runModal() == .OK, let url = panel.url { Task { await open(url.path) } }
    }

    func clearPreview() {
        selectedLayerID = nil; selectedLayerBounds = nil; pickToken = nil
        showLayers = false
        pause(); player.replaceCurrentItem(with: nil)
        generation += 1; renderBusy = false; pendingFrame = nil
        image = nil; previewKind = "empty"; warnings = []; frame = 0; timelineFrame = 0
        mediaTime = 0; mediaDuration = 0
        mediaControls.refresh()
    }

    func open(_ path: String) async {
        clearPreview(); package = nil; selectedItem = nil
        working = true; title = URL(fileURLWithPath: path).lastPathComponent
        openGeneration += 1
        let token = openGeneration
        status = "正在读取 \(title)…"
        defer { if token == openGeneration { working = false } }
        do {
            let info = try await decodedRequest(PackageInfo.self, "open", ["path": path])
            guard token == openGeneration else { return }
            accept(info)
        } catch { if token == openGeneration { fail(error) } }
    }

    func accept(_ info: PackageInfo) {
        package = info; selectedItem = nil; warnings = info.warnings
        status = "\(info.assets.count) 个素材 · \(info.animations.count) 个动画"
        if let name = info.selected_asset, let a = info.assets.first(where: { $0.name == name }) { selectedItem = "f:\(a.id)" }
        else if let a = info.animations.first { selectedItem = "a:\(a.id)" }
        else if let a = info.assets.first(where: { $0.kind == "图片" || $0.kind == "视频" || $0.kind == "音频" }) ?? info.assets.first { selectedItem = "f:\(a.id)" }
    }

    func selectItem() async {
        defer { mediaControls.refresh() }
        clearPreview()
        let token = generation
        if isSongPreview {
            title = "完整乐曲预览"; previewKind = "song"
        } else if let a = animation {
            title = a.name; warnings = a.warnings
            await render(0)
        } else if let asset {
            title = URL(fileURLWithPath: asset.name).lastPathComponent
            working = true; status = "正在读取素材…"
            defer { if token == generation { working = false } }
            do {
                let info = try await decodedRequest(PreviewInfo.self, "asset", ["asset": asset.id])
                guard token == generation else { return }
                if info.kind == "tracks", let tracks = info.assets {
                    let existing = Set(package?.assets.map(\.id) ?? [])
                    package?.assets.append(contentsOf: tracks.filter { !existing.contains($0.id) })
                    if let first = tracks.first { selectedItem = "f:\(first.id)" }
                } else if info.kind == "package", let nested = info.package {
                    accept(nested)
                } else {
                    previewKind = info.kind; warnings = info.warnings ?? []
                    if let path = info.path {
                        if info.kind == "image" {
                            let decoded = await Task.detached(priority:.userInitiated) { () -> CGImage? in
                                guard let source=CGImageSourceCreateWithURL(URL(fileURLWithPath:path) as CFURL,nil) else { return nil }
                                return CGImageSourceCreateImageAtIndex(source,0,[kCGImageSourceShouldCacheImmediately:true] as CFDictionary)
                            }.value
                            guard token == generation else { return }
                            image=decoded.map { NSImage(cgImage:$0,size:NSSize(width:$0.width,height:$0.height)) }
                        }
                        else { player.replaceCurrentItem(with: AVPlayerItem(url: URL(fileURLWithPath: path))) }
                    }
                    text = info.text ?? ""
                }
                status = package?.edit_notice.flatMap { $0.isEmpty ? nil : $0 } ?? "就绪"
            } catch { if token == generation { fail(error) } }
        }
    }

    func render(_ desired: Int) async {
        guard !pickingLayer else { return }
        guard let a = animation else { return }
        if desired != Int(frame), selectedLayerBounds != nil { selectedLayerBounds = nil }
        if renderBusy { pendingFrame = desired; return }
        let token = generation
        renderBusy = true
        defer {
            if token == generation {
                renderBusy = false
                if let pending = pendingFrame {
                    pendingFrame = nil
                    if pending != Int(frame) { Task { await render(pending) } }
                }
            }
        }
        do {
            let info = try await decodedRequest(PreviewInfo.self, "render", ["animation": a.id, "frame": desired, "transport": "shared",
                "resolution": previewResolution, "renderer": renderMode])
            guard token == generation else { return }
            if let pending = pendingFrame, pending != desired { return }
            if info.kind == "raw" { image = try rawFrames.image(info) }
            else if let path = info.path { image = NSImage(contentsOfFile: path) }
            if renderer != info.renderer { renderer = info.renderer }
            if previewKind != "image" { previewKind = "image" }
            frame = Double(info.frame ?? desired)
            let now = ProcessInfo.processInfo.systemUptime
            if !isPlaying || now - lastTimelineUpdate >= 1.0 / 30 {
                lastTimelineUpdate = now
                if timelineFrame != frame { timelineFrame = frame }
            }
            if warnings != info.warnings ?? [] { warnings = info.warnings ?? [] }
        } catch { if token == generation { pause(); fail(error) } }
    }

    func togglePlay() {
        if isPlaying && (animation != nil || player.timeControlStatus != .paused) { pause(); return }
        selectedLayerBounds = nil
        guard animation != nil || previewKind == "video" || previewKind == "audio" else { return }
        if let a = animation {
            if Int(frame) >= a.length - 1 { frame = 0 }
            baseFrame = Int(frame); startTime = ProcessInfo.processInfo.systemUptime
        } else {
            if mediaDuration > 0 && mediaTime >= mediaDuration - 0.05 { player.seek(to: .zero) }
            player.playImmediately(atRate: Float(speed))
        }
        isPlaying = true
    }
    func pause() {
        isPlaying = false; player.pause()
        if timelineFrame != frame { timelineFrame = frame }
    }
    func tick() {
        if previewKind == "audio" || previewKind == "video" {
            let duration = player.currentItem?.duration.seconds ?? 0
            let nextDuration = duration.isFinite ? max(0, duration) : 0
            let nextTime = max(0, player.currentTime().seconds)
            if mediaDuration != nextDuration { mediaDuration = nextDuration }
            if mediaTime != nextTime { mediaTime = nextTime }
            if let error = player.currentItem?.error { pause(); status = "播放失败：\(error.localizedDescription)" }
            if isPlaying && mediaDuration > 0 && mediaTime >= mediaDuration - 0.03 {
                if loop { player.seek(to: .zero); player.playImmediately(atRate: Float(speed)) }
                else { pause() }
            }
        }
        guard isPlaying, let a = animation, !renderBusy else { return }
        var desired = baseFrame + Int((ProcessInfo.processInfo.systemUptime - startTime) * playbackFPS * speed)
        if desired >= a.length {
            if loop { desired %= a.length }
            else { desired = a.length - 1; pause() }
        }
        if desired != Int(frame) { Task { await render(desired) } }
    }
    func seekFrame(_ value: Double) { pause(); timelineFrame = value; mediaControls.refresh(); Task { await render(Int(value)) } }
    func step(_ delta: Int) { if let a = animation { seekFrame(Double(max(0, min(a.length - 1, Int(timelineFrame) + delta)))) } }
    func changeSpeed() {
        baseFrame = Int(frame); startTime = ProcessInfo.processInfo.systemUptime
        if isPlaying && animation == nil { player.rate = Float(speed) }
    }

    func changeRenderSettings() {
        generation += 1; renderBusy = false; pendingFrame = nil
        baseFrame = Int(frame); startTime = ProcessInfo.processInfo.systemUptime
        if animation != nil { Task { await render(Int(frame)) } }
    }

    func inspectAnimation() {
        guard let animation, !working else { return }
        pause(); working = true; status = "正在读取动画信息…"
        Task {
            defer { working = false }
            do {
                let reply = try await decodedRequest(PreviewInfo.self, "inspect_animation", ["animation": animation.id])
                inspectionText = reply.text ?? ""; showInspection = true; status = "就绪"
            } catch { fail(error) }
        }
    }

    func animationLayers(_ id: Int) async throws -> AnimationLayersReply {
        try await decodedRequest(AnimationLayersReply.self, "animation_layers", ["animation": id, "frame": Int(frame)])
    }

    func setLayer(_ id: String, visible: Bool, animationID: Int, packageID: Int) async throws -> AnimationLayersReply {
        let reply = try await decodedRequest(AnimationLayersReply.self, "set_layer_visible",
            ["animation": animationID, "layer": id, "visible": visible, "package_id": packageID])
        if animation?.id == animationID { changeRenderSettings() }
        if let selectedLayerID, reply.layers.first(where: { $0.id == selectedLayerID })?.visible != true { self.selectedLayerID = nil; selectedLayerBounds = nil }
        return reply
    }

    func pickLayer(at point: CGPoint) async {
        guard let animation, !working, !pickingLayer else { return }
        pause(); generation += 1; renderBusy = false; pendingFrame = nil
        let token = generation
        pickToken = token
        defer { if pickToken == token { pickToken = nil } }
        do {
            let reply = try await decodedRequest(AnimationLayersReply.self, "pick_layer",
                ["animation": animation.id, "frame": Int(frame), "u": point.x, "v": point.y, "renderer": renderMode])
            guard token == generation else { return }
            selectedLayerID = reply.selected
            selectedLayerBounds = reply.bounds.flatMap { $0.count == 4 ? CGRect(x: $0[0], y: $0[1], width: $0[2], height: $0[3]) : nil }
            showLayers = reply.selected != nil
        } catch { if token == generation { fail(error) } }
    }

    func chooseReplacement(whole: Bool) {
        guard package != nil, !working, whole || canReplaceAsset else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false; panel.canChooseFiles = true
        panel.title = whole ? "选择替换资源文件" : "选择替换素材"
        panel.message = tr("替换后可预览、撤销。")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await replace(with: url, whole: whole) }
    }

    func replace(with source: URL, whole: Bool = false) async {
        guard package != nil, !working, whole || canReplaceAsset else { return }
        var args: [String: Any] = ["scope": whole ? "file" : "asset", "source": source.path]
        if let asset { args["asset"] = asset.id }
        pause(); generation += 1; renderBusy = false; pendingFrame = nil
        working = true; status = "正在替换…"
        defer { working = false }
        do {
            let result = try decode(PackageInfo.self, await request("replace", args))
            clearPreview()
            accept(result); status = result.edit_notice ?? "已替换"
        } catch { fail(error) }
    }

    func undoReplacement() {
        guard canUndoReplacement else { return }
        pause(); generation += 1; renderBusy = false; pendingFrame = nil
        working = true; status = "正在撤销…"
        Task {
            defer { working = false }
            do {
                let result = try decode(PackageInfo.self, await request("undo_replace"))
                clearPreview()
                accept(result); status = result.edit_notice ?? "已撤销替换"
            } catch { fail(error) }
        }
    }

    func saveReplacement() {
        guard let package, modified, !working else { return }
        pause()
        let panel = NSSavePanel()
        panel.title = tr("另存为")
        panel.message = tr("选择游戏目录外的新文件夹。")
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = exportName(URL(fileURLWithPath: package.path).deletingPathExtension().lastPathComponent,fallback:"resource",replaced:true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if FileManager.default.fileExists(atPath: url.path) { status = "文件夹已存在，请更名。"; return }
        working = true; status = "正在保存…"
        Task {
            defer { working = false }
            do { _ = try await request("save_replacement", ["target": url.path]); status = "已保存：\(url.path)" }
            catch { fail(error) }
        }
    }

    func export(_ kind: String) {
        guard let package else { return }
        if ["frame", "webp", "sequence"].contains(kind) && animation == nil { status = "请先选择动画"; return }
        if kind == "asset" && asset == nil { status = "请先选择素材"; return }
        pause()
        let token = generation
        let selectedAsset = asset
        let name = exportName(["textures", "audio"].contains(kind) ? URL(fileURLWithPath: package.path).deletingPathExtension().lastPathComponent : title,replaced:modified)
        var args: [String: Any] = ["kind": kind, "frame": Int(frame)]
        if let a = animation { args["animation"] = a.id }
        if let a = selectedAsset { args["asset"] = a.id }
        Task {
            guard token == generation else { return }
            working = true
            defer { if token == generation { working = false } }
            do {
                var filename = name + ["frame": "_\(Int(frame)).png", "webp": ".webp", "sequence": "_frames", "textures": "_textures", "audio": "_audio"][kind, default: ""]
                if kind == "asset", let selectedAsset { let url=URL(fileURLWithPath:selectedAsset.name);filename=exportName(url.deletingPathExtension().lastPathComponent,replaced:modified)+"."+url.pathExtension }
                guard token == generation else { return }
                let panel = NSSavePanel()
                panel.title = tr("导出素材"); panel.canCreateDirectories = true; panel.nameFieldStringValue = filename
                guard panel.runModal() == .OK, let url = panel.url else { return }
                if FileManager.default.fileExists(atPath: url.path) { status = "目标已存在，请更名。"; return }
                args["target"] = url.path
                status = "正在导出…"
                _ = try await request("export", args)
                if token == generation { status = "已导出：\(url.path)" }
            } catch { if token == generation { fail(error) } }
        }
    }
    func fail(_ error: Error) { status = error.localizedDescription; warnings = [error.localizedDescription] }
    func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { data, _ in
            guard let data = data as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) else { return }
            DispatchQueue.main.async {
                var isDirectory: ObjCBool = false
                FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
                Task { if isDirectory.boolValue { await self.scan(url.path) } else { await self.open(url.path) } }
            }
        }
        return true
    }
}
