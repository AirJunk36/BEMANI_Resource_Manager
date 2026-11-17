import AVKit
import Combine
import SwiftUI
import UniformTypeIdentifiers

#if !LAYOUT_TEST
@main
struct IIDXViewerApp: App {
    @NSApplicationDelegateAdaptor(ViewerApplicationDelegate.self) private var appDelegate
    @StateObject private var model = ViewerModel()
    @ObservedObject private var language = AppLanguage.shared

    init() { ViewerApplicationDelegate.applyDockIcon() }
    var body: some Scene {
        WindowGroup(tr("BEMANI素材管理器")) {
            ViewerWindow(model: model)
                .frame(minWidth: 1120, minHeight: 720)
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in model.mediaControls.stop(); model.backend?.stop() }
        }
        .defaultSize(width: 1460, height: 920)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button(tr("关于 BEMANI素材管理器")) { NSApplication.shared.orderFrontStandardAboutPanel(options:[.applicationVersion:Bundle.main.object(forInfoDictionaryKey:"BEMANIDisplayVersion") as? String ?? "1.0.0 Beta2"]) }
            }
            CommandMenu("Language / 语言") {
                Picker("Language / 语言",selection:$language.code) {
                    Text("简体中文").tag("zh-Hans")
                    Text("English").tag("en")
                }
            }
            CommandGroup(replacing: .newItem) {
                Button(tr("打开游戏目录…"), action: model.chooseFolder).keyboardShortcut("o", modifiers: [.command, .shift])
                Button(tr("打开素材文件…"), action: model.chooseFile).keyboardShortcut("o")
            }
            CommandMenu(tr("播放")) {
                Button(tr(model.isPlaying ? "暂停" : "播放"), action: model.togglePlay)
                Button(tr("上一帧")) { model.step(-1) }
                Button(tr("下一帧")) { model.step(1) }
            }
            CommandMenu(tr("素材替换")) {
                Button(tr("替换素材…")) { model.chooseReplacement(whole: false) }.disabled(!model.canReplaceAsset).keyboardShortcut("r", modifiers: [.command, .shift])
                Button(tr("替换资源文件…")) { model.chooseReplacement(whole: true) }.disabled(model.package == nil || model.working)
                Divider()
                Button(tr("撤销替换"), action: model.undoReplacement).disabled(!model.canUndoReplacement)
                Button(tr("另存为…"), action: model.saveReplacement).disabled(!model.modified || model.working).keyboardShortcut("s", modifiers: [.command, .shift])
            }
        }
    }
}
#endif

struct ViewerWindow: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    var startAutomatically = true
    var layoutObserver: (([String: CGRect]) -> Void)? = nil
    var controlLayoutObserver: (([String: [CGRect]]) -> Void)? = nil
    @State private var showHelp = false
    @State private var collapsedCategories: Set<String> = []

    private func categoryHeader(_ name: String, count: Int) -> some View {
        Button { if !collapsedCategories.insert(name).inserted { collapsedCategories.remove(name) } } label: {
            HStack {
                Image(systemName: collapsedCategories.contains(name) && model.assetSearch.isEmpty ? "chevron.right" : "chevron.down")
                Text("\(tr(name)) · \(count)")
            }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("category.\(name)")
    }

    private var previewHeading: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(model.displayTitle).font(.headline).lineLimit(1).help(model.displayTitle)
            if let a = model.animation {
                HStack(spacing: 4) {
                    Picker(tr("预览分辨率"), selection: $model.previewResolution) {
                        Text(tr("720p")).tag("720p"); Text(tr("1080p")).tag("1080p")
                        Text(tr("720p（竖屏）")).tag("720p-portrait")
                        Text(tr("1080p（竖屏）")).tag("1080p-portrait")
                        Text(tr("480p")).tag("480p")
                    }.frame(width: appLanguage.english ? 145 : 126).help(tr("原始尺寸 \(a.width) × \(a.height)；小素材不放大"))
                    Text(tr("·"))
                    Picker(tr("动画帧率"), selection: $model.playbackFPS) {
                        Text(tr("60 FPS")).tag(60.0); Text(tr("120 FPS")).tag(120.0)
                    }.frame(width: 80)
                    Text(tr("·"))
                    Picker(tr("渲染方式"), selection: $model.renderMode) {
                        Text(tr("CPU")).tag("cpu"); Text(tr("GPU")).tag("gpu")
                    }.frame(width: 58).help(tr(model.renderer?.hardware == true ? model.renderer?.device ?? "" : model.renderer?.reason ?? "CPU"))
                }.pickerStyle(.menu).labelsHidden().controlSize(.mini).font(.caption).foregroundStyle(.secondary).fixedSize()
            } else if let a = model.asset {
                Text(tr(a.details.isEmpty ? a.kind : a.details)).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var previewTools: some View {
        HStack {
            Picker(tr("背景"), selection: $model.background) { Text(tr("棋盘")).tag("棋盘"); Text(tr("黑色")).tag("黑色"); Text(tr("白色")).tag("白色") }.labelsHidden().frame(width: appLanguage.english ? 120 : 72)
            Picker(tr("屏幕比例"), selection: $model.scaleMode) {
                ForEach(PreviewScale.allCases, id: \.self) { Text(tr($0.rawValue)).tag($0) }
            }.labelsHidden().frame(width: 80).help(tr("1:1 原尺寸；适应保留比例；拉伸填满"))
            Menu {
                Button(tr("当前帧 PNG")) { model.export("frame") }.disabled(model.animation == nil)
                Button(tr("动画 WebP")) { model.export("webp") }.disabled(model.animation == nil)
                Button(tr("动画 / 视频 MP4…"), action: model.exportMP4).disabled(model.animation == nil && model.asset?.kind != "视频")
                Button(tr("导出 GIF…"), action: model.exportGIF).disabled(model.animation == nil && model.asset?.kind != "视频")
                Button(tr("PNG 序列")) { model.export("sequence") }.disabled(model.animation == nil)
                Divider()
                Button(tr("当前素材")) { model.export("asset") }.disabled(model.asset == nil)
                Button(tr("全部纹理 PNG")) { model.export("textures") }
                Button(tr("解包音频…")) { model.export("audio") }
                Divider()
                Button(tr("音频/视频导出…")) { model.mediaControls.perform(.pause); model.showSongExport = true }.disabled(!model.canOpenSongStudio)
            } label: { Label(tr("导出"), systemImage: "square.and.arrow.up") }.disabled(model.package == nil || model.working)
            if model.package?.format == "live2d" {
                Button(tr("编辑 Live2D")) {
                    model.pause()
                    if model.animation==nil,let first=model.package?.animations.first {model.selectedItem="a:\(first.id)"}
                    model.showLiveEditor = true
                }
            } else {
                Button(tr("动画信息")) { model.inspectAnimation() }.disabled(model.animation == nil || model.working)
            }
            Button(tr("素材列表")) { model.showLayers = true }.disabled(model.animation == nil || model.working)
                .help(tr("点击预览选中素材"))
                .popover(isPresented: $model.showLayers) {
                    if let animation = model.animation {
                        AnimationLayersPanel(animationID: animation.id, selectedID: model.selectedLayerID, load: { try await model.animationLayers(animation.id) },
                            update: { id, shown, packageID in try await model.setLayer(id, visible: shown, animationID: animation.id, packageID: packageID) })
                            .equatable()
                    }
                }
        }.fixedSize()
    }

    var body: some View {
        VStack(spacing: 0) {
        GeometryReader { viewport in
        NavigationSplitView {
            ResourceCatalogSidebar(model:model,catalog:model.library)
                .navigationSplitViewColumnWidth(min:260,ideal:330,max:480).disabled(model.working)
        } content: {
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(model.package?.displayTitle ?? tr("包内素材")).font(.headline).lineLimit(2).help(model.package?.displayTitle ?? tr("包内素材"))
                    if let engine = model.package?.engine { Text(tr(engine.label)).font(.caption).foregroundStyle(.secondary).help(tr(engine.detail)) }
                    TextField(tr("搜索素材"), text: $model.assetSearch).textFieldStyle(.roundedBorder)
                }.padding(12)
                List(selection: Binding<String?>(get: { model.selectedItem }, set: { value in
                    if let value { model.selectedItem = value }
                })) {
                    if let package = model.package {
                        if model.hasSongPreview && (model.assetSearch.isEmpty || tr("完整乐曲预览").localizedCaseInsensitiveContains(model.assetSearch)) {
                            Section {
                                if !collapsedCategories.contains("预览") || !model.assetSearch.isEmpty {
                                    Label(tr("完整乐曲预览"),systemImage:"music.note.list").tag("song")
                                }
                            } header: { categoryHeader("预览",count:1) }
                        }
                        if !package.animations.isEmpty {
                            Section {
                              if !collapsedCategories.contains("动画") || !model.assetSearch.isEmpty {
                                ForEach(model.filteredAnimations) { a in
                                    Label(a.name, systemImage: "play.rectangle").lineLimit(1).help(a.name).tag("a:\(a.id)")
                                }
                              }
                            } header: { categoryHeader("动画", count: package.animations.count) }
                        }
                        ForEach(model.groups, id: \.self) { group in
                            let assets = model.assets(in: group)
                            if !assets.isEmpty {
                                Section {
                                  if !collapsedCategories.contains(group) || !model.assetSearch.isEmpty {
                                    ForEach(assets) { asset in
                                        Text(asset.name).lineLimit(1).truncationMode(.middle).help(asset.details.isEmpty ? asset.name : tr(asset.details)).tag("f:\(asset.id)")
                                    }
                                  }
                                } header: { categoryHeader(group, count: assets.count) }
                            }
                        }
                    }
                }.listStyle(.inset)
            }.navigationSplitViewColumnWidth(min: 220, ideal: 290, max: 420).disabled(model.working)
        } detail: {
            GeometryReader { detail in
            VStack(spacing: 0) {
                ViewThatFits(in: .horizontal) {
                    HStack { previewHeading; Spacer(minLength: 12); previewTools }
                    VStack(alignment: .leading, spacing: 8) {
                        previewHeading
                        HStack { Spacer(); previewTools }
                    }
                }.padding(14).fixedSize(horizontal: false, vertical: true).layoutPriority(1)
                if model.isSongPreview {
                    SongExportView(model:model,previewOnly:true).id(model.package?.path ?? "")
                } else { PreviewSurface(model: model, preview: model.preview) }
                if !model.warnings.isEmpty {
                    HStack(alignment: .top) {
                        Image(systemName: "info.circle").foregroundStyle(.orange)
                        Text(tr(model.warnings.prefix(4).joined(separator: "\n"))).font(.caption).textSelection(.enabled)
                        Spacer(minLength: 0)
                    }.padding(10).background(.orange.opacity(0.07))
                }
                if !model.isSongPreview { PlaybackControls(model: model, measureLayout: controlLayoutObserver != nil)
                    .disabled(model.working)
                    .fixedSize(horizontal: false, vertical: true)
                    .layoutPriority(2)
                    .layoutFrame("playback", enabled: layoutObserver != nil)
                }
            }
            // Native navigation columns can start below the browser's origin
            // without reducing their height. Keep the detail inside the visible
            // browser viewport, including the space reserved for the status row.
            .frame(width: detail.size.width, height: max(0, viewport.size.height - max(0,
                detail.frame(in: .named("viewer-root")).minY - viewport.frame(in: .named("viewer-root")).minY)), alignment: .top)
            }
        }
        .frame(width: viewport.size.width, height: viewport.size.height)
        }
        .frame(minHeight: 0, maxHeight: .infinity)
        Divider()
        HStack {
            Text(tr(model.status)).lineLimit(1).textSelection(.enabled)
            Spacer()
        }.font(.caption).padding(.horizontal, 14).padding(.vertical, 7).background(.bar)
            .fixedSize(horizontal: false, vertical: true)
            .layoutFrame("status", enabled: layoutObserver != nil)
        }
        .toolbar {
            ToolbarItemGroup {
                Button(action: model.chooseFolder) { Label(tr("打开目录"), systemImage: "folder.badge.plus") }
                Button(action: model.chooseFile) { Label(tr("打开文件"), systemImage: "doc.badge.plus") }
                Menu {
                    Button(tr("替换素材…")) { model.chooseReplacement(whole: false) }.disabled(!model.canReplaceAsset)
                    Button(tr("替换资源文件…")) { model.chooseReplacement(whole: true) }
                    Divider()
                    Button(tr("撤销替换"), action: model.undoReplacement).disabled(!model.canUndoReplacement)
                    Button(tr("另存为…"), action: model.saveReplacement).disabled(!model.modified)
                } label: { Label(tr("替换"), systemImage: "arrow.triangle.2.circlepath") }.disabled(model.package == nil || model.working || model.package?.format == "live2d")
                Button { showHelp = true } label: { Label(tr("帮助"), systemImage: "questionmark.circle") }
            }
        }
        .coordinateSpace(name: "viewer-root")
        .onPreferenceChange(ViewerLayoutFrames.self) { layoutObserver?($0) }
        .onPreferenceChange(PlaybackLayoutFrames.self) { controlLayoutObserver?($0) }
        .task { model.mediaControls.attach(model); if startAutomatically { await model.startup() } }
        .task(id: model.selectedFile) {
            guard let path = model.selectedFile else { return }
            do { try await Task.sleep(for: .milliseconds(90)); try Task.checkCancellation(); await model.open(path) } catch { }
        }
        .task(id: model.selectedItem) {
            guard model.selectedItem != nil else { return }
            do { try await Task.sleep(for: .milliseconds(35)); try Task.checkCancellation(); await model.selectItem() } catch { }
        }
        .onChange(of: model.speed) { model.changeSpeed() }
        .onChange(of: model.playbackFPS) { model.changeSpeed() }
        .onChange(of: model.previewResolution) { model.changeRenderSettings() }
        .onChange(of: model.renderMode) { model.changeRenderSettings() }
        .onDrop(of: [UTType.fileURL], isTargeted: nil, perform: model.handleDrop)
        .sheet(isPresented: $model.showSongExport) { SongExportView(model: model) }
        .sheet(isPresented: $model.showLiveEditor) { Live2DEditor(model: model) }
        .sheet(isPresented: $model.showVideoExport) {
            if let job = model.videoJob { VideoExportProgress(job:job,visible:$model.showVideoExport) }
        }
        .sheet(isPresented: $model.showInspection) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(tr("动画信息")).font(.title2.bold())
                    Spacer()
                    Button(tr("关闭")) { model.showInspection = false }.keyboardShortcut(.cancelAction)
                }
                ScrollView([.horizontal, .vertical]) {
                    Text(tr(model.inspectionText)).font(.system(.body, design: .monospaced))
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(8)
                }
            }.padding(20).frame(minWidth: 820, minHeight: 560)
        }
        .sheet(isPresented: $showHelp) {
            VStack(alignment: .leading, spacing: 16) {
                Text(tr("BEMANI素材管理器")).font(.title2.bold())
                Text(tr("打开目录或文件，选择素材预览。\n空格播放 / 暂停，方向键逐帧，拖动进度条定位。\n导出支持 PNG、WebP、PNG 序列和原文件。"))
                Text(tr("替换后可预览、撤销；另存时选择新文件夹。"))
                Text(tr("支持 IFS、BIN/TXP2、IDX/GCZ。旧版素材需保留关联文件；部分效果可能不完整。")).foregroundStyle(.secondary)
                HStack { Spacer(); Button(tr("完成")) { showHelp = false }.keyboardShortcut(.defaultAction) }
            }.padding(28).frame(width: 550)
        }
    }
}

private struct PreviewSurface: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @ObservedObject var preview: PreviewState
    var body: some View {
        ZStack {
                    Color(nsColor: .underPageBackgroundColor)
                    if model.previewKind == "image", let image = model.image {
                        NativeImagePreview(image: image, mode: model.scaleMode, background: model.background,
                            selectedBounds: model.selectedLayerBounds,
                            onPick: model.animation == nil ? nil : { point in Task { await model.pickLayer(at: point) } }).id(model.selectedItem)
                    } else if model.previewKind == "video" {
                        NativeVideoPreview(player: model.player, mode: model.scaleMode)
                    } else if model.previewKind == "audio" {
                        VStack(spacing: 18) {
                            Image(systemName: "waveform.circle").font(.system(size: 76, weight: .ultraLight)).foregroundStyle(.tint)
                            Text(model.displayTitle).font(.title3)
                            Text(tr("空格播放 / 暂停")).foregroundStyle(.secondary)
                        }
                    } else if model.previewKind == "text" {
                        ScrollView { Text(model.text).font(.system(.body, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding() }
                    } else {
                        VStack(spacing: 14) {
                            Image(systemName: "photo.on.rectangle.angled").font(.system(size: 58, weight: .ultraLight)).foregroundStyle(.secondary)
                            Text(tr("浏览素材")).font(.title3)
                            Text(tr("动画 · 图像 · 音频 · BGA")).foregroundStyle(.secondary)
                            Button(tr("打开游戏目录…"), action: model.chooseFolder)
                        }
                    }
                    if model.working { ProgressView().controlSize(.large).padding(24).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)) }
                }.frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity).clipped()
    }
}

private struct PlaybackControls: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @ObservedObject var preview: PreviewState
    var measureLayout = false

    init(model: ViewerModel, measureLayout: Bool = false) {
        self.model = model; self.preview = model.preview; self.measureLayout = measureLayout
    }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                transport
                timeline
                options
            }
            VStack(spacing: 8) {
                timeline
                HStack(spacing: 10) {
                    transport
                    Spacer(minLength: 12)
                    options
                }
            }
        }.padding(.horizontal, 12).padding(.vertical, 10)
    }

    private var transport: some View {
        HStack(spacing: 8) {
            Button(action: model.togglePlay) { Image(systemName: model.isPlaying ? "pause.fill" : "play.fill").frame(width: 18) }.disabled(model.animation == nil && model.previewKind != "audio" && model.previewKind != "video").accessibilityLabel(tr(model.isPlaying ? "暂停" : "播放")).help(tr("空格：播放 / 暂停")).accessibilityIdentifier("playback.play").controlFrame("play", enabled: measureLayout)
            Button { model.step(-1) } label: { Image(systemName: "backward.frame") }.disabled(model.animation == nil).accessibilityIdentifier("playback.previous").controlFrame("previous", enabled: measureLayout)
            Button { model.step(1) } label: { Image(systemName: "forward.frame") }.disabled(model.animation == nil).accessibilityIdentifier("playback.next").controlFrame("next", enabled: measureLayout)
        }.fixedSize()
    }

    private var timeline: some View {
        HStack(spacing: 8) {
            if let a = model.animation {
                Slider(value: Binding(get: { model.timelineFrame }, set: { model.seekFrame($0) }), in: 0...Double(max(1, a.length - 1)), step: 1).frame(minWidth: 140).accessibilityIdentifier("playback.timeline").controlFrame("timeline", enabled: measureLayout)
                Text(tr("\(Int(model.timelineFrame) + 1) / \(a.length)")).monospacedDigit().font(.caption).frame(minWidth: 70, alignment: .trailing)
            } else {
                Slider(value: Binding(get: { model.mediaTime }, set: { value in model.player.seek(to: CMTime(seconds: value, preferredTimescale: 600)) }), in: 0...max(1, model.mediaDuration)).disabled(model.mediaDuration == 0).frame(minWidth: 140).accessibilityIdentifier("playback.timeline").controlFrame("timeline", enabled: measureLayout)
                Text("\(model.mediaTime, specifier: "%.1f") / \(model.mediaDuration, specifier: "%.1f")s").monospacedDigit().font(.caption).frame(minWidth: 70, alignment: .trailing)
            }
        }
    }

    private var options: some View {
        HStack(spacing: 10) {
            Toggle(tr("循环"), isOn: $model.loop).toggleStyle(.checkbox).accessibilityIdentifier("playback.loop").controlFrame("loop", enabled: measureLayout)
            Picker(tr("速度"), selection: $model.speed) { ForEach([0.25, 0.5, 1, 1.5, 2], id: \.self) { Text(tr("\($0.formatted())×")).tag($0) } }.labelsHidden().frame(width: 74).accessibilityIdentifier("playback.speed").controlFrame("speed", enabled: measureLayout)
        }.fixedSize()
    }
}

private struct ViewerLayoutFrames: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private struct PlaybackLayoutFrames: PreferenceKey {
    static var defaultValue: [String: [CGRect]] = [:]
    static func reduce(value: inout [String: [CGRect]], nextValue: () -> [String: [CGRect]]) {
        value.merge(nextValue(), uniquingKeysWith: +)
    }
}

extension View {
    func controlFrame(_ name: String, enabled: Bool) -> some View {
        background {
            if enabled {
                GeometryReader { geometry in
                    Color.clear.preference(key: PlaybackLayoutFrames.self, value: [name: [geometry.frame(in: .named("viewer-root"))]])
                }
            }
        }
    }

    func layoutFrame(_ name: String, enabled: Bool) -> some View {
        background {
            if enabled {
                GeometryReader { geometry in
                    Color.clear.preference(key: ViewerLayoutFrames.self, value: [name: geometry.frame(in: .named("viewer-root"))])
                }
            }
        }
    }
}

struct NativeImagePreview: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let image: NSImage
    let mode: PreviewScale
    let background: String
    var selectedBounds: CGRect? = nil
    var onPick: ((CGPoint) -> Void)? = nil
    @State private var localZoom = 1.0
    @State private var offset = CGSize.zero
    @State private var previousOffset = CGSize.zero
    var body: some View {
        GeometryReader { geometry in
            let available = mode == .stretch ? geometry.size : CGSize(width: max(1, geometry.size.width - 36), height: max(1, geometry.size.height - 36))
            let size = mode.size(image.size, in: available)
            ZStack {
                if background == "棋盘" {
                    Canvas { context, size in
                        for y in stride(from: 0.0, to: size.height, by: 16) {
                            for x in stride(from: 0.0, to: size.width, by: 16) {
                                let light = (Int(x / 16) + Int(y / 16)) % 2 == 0
                                context.fill(Path(CGRect(x: x, y: y, width: 16, height: 16)), with: .color(Color(white: light ? 0.94 : 0.84)))
                            }
                        }
                    }
                } else { (background == "白色" ? Color.white : Color.black) }
                Image(nsImage: image).resizable().interpolation(.high).frame(width: max(1, size.width * localZoom), height: max(1, size.height * localZoom)).offset(offset)
                if let bounds = selectedBounds {
                    Rectangle().stroke(Color.accentColor, lineWidth: 1.5)
                        .frame(width: max(1, bounds.width * size.width * localZoom), height: max(1, bounds.height * size.height * localZoom))
                        .offset(x: (bounds.midX - 0.5) * size.width * localZoom + offset.width,
                                y: (bounds.midY - 0.5) * size.height * localZoom + offset.height).allowsHitTesting(false)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height).clipped()
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { value in
                if hypot(value.translation.width, value.translation.height) > 4 {
                    offset = CGSize(width: previousOffset.width + value.translation.width, height: previousOffset.height + value.translation.height)
                }
            }.onEnded { value in
                if hypot(value.translation.width, value.translation.height) <= 4, let onPick {
                    onPick(PreviewGeometry.normalized(value.location, viewport: geometry.size, image: size, zoom: localZoom, offset: offset))
                }
                previousOffset = offset
            })
            .gesture(MagnificationGesture().onChanged { value in localZoom = max(0.05, min(20, value)) })
            .onTapGesture(count: 2) { localZoom = 1; offset = .zero; previousOffset = .zero }
            .onChange(of: mode) { localZoom = 1; offset = .zero; previousOffset = .zero }
        }
    }
}

struct NativeVideoPreview: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let player: AVPlayer
    let mode: PreviewScale
    var body: some View {
        GeometryReader { geometry in
            let source = player.currentItem?.presentationSize ?? .zero
            let size = mode.size(source.width > 0 && source.height > 0 ? source : CGSize(width: 1920, height: 1080), in: geometry.size)
            NativeVideoSurface(player: player).frame(width: size.width, height: size.height)
                .frame(width: geometry.size.width, height: geometry.size.height).clipped()
        }
    }
}

struct NativeVideoSurface: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.updatesNowPlayingInfoCenter = false
        view.controlsStyle = .none; view.videoGravity = .resize; view.player = player
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
}
