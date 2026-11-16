import AppKit
import AVKit
import SwiftUI

struct SongBank: Decodable, Identifiable { let id: String; let name: String }
struct SongChart: Decodable, Identifiable { let id: Int; let name: String; let bank: String? }
struct SongInfo: Decodable { let banks: [SongBank]; let bank: String; let charts: [SongChart]; let needs_chart: Bool }

struct SongExportView: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @StateObject private var studio: SongStudio
    private let previewOnly: Bool
    init(model: ViewerModel, previewOnly: Bool = false) {
        self.previewOnly = previewOnly
        self.model = model; _studio = StateObject(wrappedValue:SongStudio(model:model))
    }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            if !previewOnly { HStack {
                Text(tr("音频/视频导出")).font(.title2.bold()).lineLimit(1)
                Spacer()
                if !studio.originalAudio {
                    Picker(tr("内容"),selection:$studio.videoMode) { Text(tr("音频")).tag(false); Text(tr("视频")).tag(true) }
                        .pickerStyle(.segmented).labelsHidden().frame(width:180).disabled(studio.busy)
                }
            } }
            if previewOnly { NativeMediaPlayer(player:studio.player).frame(minHeight:160,maxHeight:.infinity).layoutPriority(1) }
            ScrollView {
                VStack(alignment:.leading,spacing:12) {
                    if previewOnly {
                        if let info = studio.info {
                            HStack {
                                Picker(tr("谱面"),selection:$studio.chart) { ForEach(info.charts) { Text(tr($0.name)).tag($0.id) } }
                                Button(tr("选择谱面…")) { studio.choose("chart") }
                            }
                            if info.needs_chart { Text(tr("需要配套 .1 谱面")).font(.caption).foregroundStyle(.secondary) }
                        }
                        HStack {
                            Button(tr("全部")) {
                                let all = (studio.bgaPath.isEmpty || studio.useBGA) && (studio.layerPath.isEmpty || studio.useLayer)
                                studio.useBGA = !all && !studio.bgaPath.isEmpty
                                studio.useLayer = !all && !studio.layerPath.isEmpty
                            }.disabled(studio.bgaPath.isEmpty && studio.layerPath.isEmpty)
                            Toggle(tr("BGA"),isOn:$studio.useBGA).disabled(studio.bgaPath.isEmpty)
                            Toggle(tr("Layer"),isOn:$studio.useLayer).disabled(studio.layerPath.isEmpty)
                            Toggle(tr("循环"),isOn:$model.loop)
                            Spacer()
                            Menu(tr("选择素材")) { Button(tr("BGA…")) { studio.choose("bga") }; Button(tr("Layer…")) { studio.choose("layer") } }
                        }.toggleStyle(.checkbox)
                        if studio.useLayer && !studio.layerChoices.isEmpty {
                            Picker(tr("Layer"),selection:$studio.layerAnimation) { ForEach(studio.layerChoices) { Text(tr($0.name)).tag($0.id) } }
                            layerStateSettings
                        }
                    } else {
                    if studio.originalAsset != nil && model.canExportSong {
                        Picker(tr("来源"),selection:$studio.originalAudio) { Text(tr("完整乐曲")).tag(false); Text(tr("原音轨")).tag(true) }.pickerStyle(.segmented)
                    }
                    if studio.originalAudio {
                        Text(tr(studio.originalAsset?.name ?? "")).lineLimit(1)
                        Text(tr("原格式：\(URL(fileURLWithPath:studio.originalName).pathExtension.uppercased())")).font(.caption).foregroundStyle(.secondary)
                    } else {
                    HStack {
                        if !studio.songs.isEmpty {
                            Picker(tr("对应乐曲"),selection:$studio.songPath) { ForEach(studio.songs) { Text(tr($0.name)).tag($0.path) } }
                        }
                        Spacer(); Button(tr("选择乐曲…"),action:studio.chooseSong)
                    }
                    if let info = studio.info {
                        HStack {
                            Picker(tr("音频包"),selection:$studio.bank) { ForEach(info.banks) { Text(tr($0.name)).tag($0.id) } }
                            Picker(tr("谱面"),selection:$studio.chart) { ForEach(info.charts) { Text(tr($0.name)).tag($0.id) } }
                            Button(tr("选择谱面…")) { studio.choose("chart") }
                        }
                        if info.needs_chart { Text(tr("需要配套 .1 谱面")).font(.caption).foregroundStyle(.secondary) }
                    }
                    if studio.videoMode { videoSettings }
                    }
                    }
                }
            }.frame(height:previewOnly ? (studio.layerStates.isEmpty ? 120 : 175) : nil).frame(maxHeight:previewOnly ? (studio.layerStates.isEmpty ? 120 : 175) : studio.videoMode ? 330 : 130).disabled(studio.busy)
            Text(tr(studio.status)).font(.caption).lineLimit(2).textSelection(.enabled)
            HStack {
                if studio.busy { ProgressView().controlSize(.small) }
                if previewOnly {
                    Button(tr("播放")) { if studio.player.currentItem != nil { studio.player.play() } else { studio.preview(video:studio.useBGA || studio.useLayer) } }.disabled(studio.busy || !studio.canPreview)
                    Button(tr("暂停")) { studio.player.pause() }
                }
                Spacer()
                if studio.busy { Button(tr("取消处理"),action:studio.cancel) }
                else if !previewOnly {
                    Button(tr("关闭")) { model.showSongExport = false }.keyboardShortcut(.cancelAction)
                    if studio.videoMode && !studio.originalAudio {
                        Button(tr("导出 GIF…")) { studio.export(video:true,gif:true) }.disabled(!studio.canPreview).help(tr("GIF：50 FPS，无音频"))
                    }
                    Button(tr(studio.originalAudio ? "导出原格式…" : studio.videoMode ? "导出 MP4…" : "导出 WAV…")) { studio.export(video:studio.videoMode) }.disabled(!studio.canPreview)
                }
            }
        }.padding(previewOnly ? 14 : 22).frame(width:previewOnly ? nil : 720).interactiveDismissDisabled(studio.busy)
            .task { if previewOnly { model.mediaControls.use(studio) }; studio.load(preview:previewOnly) }
            .onDisappear { studio.cancel(); if previewOnly { model.mediaControls.use(nil) } }
            .onChange(of:studio.originalAudio) { studio.changeAudioSource() }
            .onChange(of:studio.bank) { if !studio.busy { studio.refresh() } }
            .onChange(of:studio.chart) { if !studio.busy { studio.chartChanged(); refreshPreview() } }
            .onChange(of:studio.songPath) { if !studio.busy { studio.songChanged() } }
            .onChange(of:studio.videoMode) { if !previewOnly { studio.prepareLayer() } }
            .onChange(of:studio.useBGA) { refreshPreview() }
            .onChange(of:studio.useLayer) { refreshPreview() }
            .onChange(of:studio.bgaPath) { refreshPreview() }
            .onChange(of:studio.layerPath) { refreshPreview() }
            .onChange(of:studio.layerAnimation) { refreshPreview() }
            .onChange(of:studio.layerState) { refreshPreview(restart:true) }
            .onReceive(NotificationCenter.default.publisher(for:.AVPlayerItemDidPlayToEndTime)) { event in
                guard previewOnly, model.loop, let item = event.object as? AVPlayerItem, item === studio.player.currentItem else { return }
                studio.player.seek(to:.zero); studio.player.play()
            }
    }
    private func refreshPreview(restart: Bool = false) {
        guard previewOnly, !studio.busy, studio.canPreview else { return }
        studio.preview(video:studio.useBGA || studio.useLayer,autoplay:studio.player.rate > 0,preservePosition:!restart)
    }
    private var layerStateSettings: some View {
        HStack {
            if !studio.layerStates.isEmpty {
                Picker(tr("状态"),selection:$studio.layerState) { ForEach(studio.layerStates) { Text(tr($0.name)).tag($0.id) } }
                if studio.hasLayerGauge {
                    Text(tr("血量"));Slider(value:$studio.layerGauge,in:0...100,step:1,onEditingChanged:{ editing in if !editing { refreshPreview() } }).frame(maxWidth:150)
                    Text(tr("\(Int(studio.layerGauge))%")).monospacedDigit().frame(width:40)
                }
            }
        }
    }
    private var videoSettings: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack {
                Toggle(tr("BGA"),isOn:$studio.useBGA).frame(width:70,alignment:.leading)
                TextField(tr("选择或输入 BGA 路径"),text:Binding(get:{ studio.bgaPath },set:{ studio.bgaPath = $0; studio.useBGA = !$0.isEmpty }))
                    .textFieldStyle(.roundedBorder).onSubmit { studio.setVisualPath("bga",studio.bgaPath) }
                Button(tr("选择 BGA…")) { studio.choose("bga") }
                Button { studio.setVisualPath("bga","") } label: { Image(systemName:"xmark.circle") }.help(tr("清除 BGA")).disabled(studio.bgaPath.isEmpty)
            }
            HStack {
                Toggle(tr("循环"),isOn:$studio.bgaLoop)
                Text(tr("偏移（秒）")); TextField(tr("0"),value:$studio.bgaOffset,format:.number).frame(width:85).help(tr("负数提前播放"))
            }
            HStack {
                Toggle(tr("Layer"),isOn:$studio.useLayer).frame(width:70,alignment:.leading)
                TextField(tr("选择或输入 Layer 路径"),text:Binding(get:{ studio.layerPath },set:{ studio.layerPath = $0; studio.useLayer = !$0.isEmpty }))
                    .textFieldStyle(.roundedBorder).onSubmit { studio.setVisualPath("layer",studio.layerPath) }
                Button(tr("选择 Layer…")) { studio.choose("layer") }
                Button { studio.setVisualPath("layer","") } label: { Image(systemName:"xmark.circle") }.help(tr("清除 Layer")).disabled(studio.layerPath.isEmpty)
            }
            HStack {
                Toggle(tr("循环"),isOn:$studio.layerLoop).disabled(studio.layerAnimation == -2)
                Text(tr("偏移（秒）")); TextField(tr("0"),value:$studio.layerOffset,format:.number).frame(width:85)
                if studio.useLayer && !studio.layerChoices.isEmpty {
                    Picker(tr("Layer 动画"),selection:$studio.layerAnimation) { ForEach(studio.layerChoices) { Text(tr($0.name)).tag($0.id) } }
                }
                Toggle(tr("黑色透明"),isOn:$studio.blackKey)
            }
            HStack {
                Picker(tr("分辨率"),selection:$studio.resolution) {
                    Text(tr("480p")).tag("480p"); Text(tr("720p")).tag("720p"); Text(tr("1080p")).tag("1080p")
                }
                Picker(tr("帧率"),selection:$studio.fps) { Text(tr("60 FPS")).tag(60); Text(tr("120 FPS")).tag(120) }
            }
            layerStateSettings
        }
    }
}

struct NativeMediaPlayer: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView(); view.player = player; view.controlsStyle = .inline; view.videoGravity = .resizeAspect
        view.updatesNowPlayingInfoCenter = false
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
}
