import AppKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class BackgroundCore {
    let backend: Backend
    var context: [String: Any]
    init(home: URL, context: [String: Any]) throws {
        self.context = context; backend = try Backend(home: home, background: true)
    }
    func open() async throws {
        _ = try await backend.request("open", ["path": context["path"]!, "protected_roots": context["protected_roots"] ?? []])
        if let pose=context["live2d_pose"] as? [[String:Any]],!pose.isEmpty {
            _ = try await backend.request("live2d_edit",["operation":"pose","keys":pose])
        }
        try Task.checkCancellation()
    }
    func request(_ method: String, _ arguments: [String: Any] = [:]) async throws -> [String: Any] {
        var args = arguments; args["context_path"] = context["context_path"]
        let reply = try await backend.request(method,args); try Task.checkCancellation(); return reply
    }
    func decoded<T: Decodable>(_ type: T.Type, _ method: String, _ arguments: [String: Any] = [:]) async throws -> T {
        let result = try await request(method,arguments)
        return try await Task.detached { try decode(type,result) }.value
    }
    func stop() { backend.stop() }
    func openSong(path: String, workingPath: String? = nil) async throws {
        _ = try await backend.request("open",["path":workingPath ?? path,"protected_roots":context["protected_roots"] ?? []])
        try Task.checkCancellation()
        context["path"] = workingPath ?? path; context["context_path"] = path;context["modified"] = workingPath != nil && workingPath != path
    }
}

@MainActor
final class VideoExportJob: ObservableObject {
    var format = "mp4"
    @Published var busy = true
    @Published var status = "正在导出 MP4…"
    var core: BackgroundCore?
    var task: Task<Void,Never>?
    func cancel() { task?.cancel(); core?.stop(); core = nil; busy = false }
    func start(model: ViewerModel, target: URL, arguments: [String: Any]) {
        format=arguments["kind"] as? String ?? "mp4"
        status=format=="gif" ? "正在导出 GIF…" : "正在导出 MP4…"
        task = Task {
            defer { core?.stop(); core = nil; busy = false }
            do {
                let context = try await model.request("export_context",arguments)
                try Task.checkCancellation()
                core = try BackgroundCore(home:model.home,context:context)
                try await core!.open()
                var args = arguments; args["kind"] = format; args["target"] = target.path
                args["hidden_layers"] = context["hidden_layers"]
                _ = try await core!.request("export",args)
                status = "已导出：\(target.path)"; model.status = status
            } catch {
                if !Task.isCancelled { status = error.localizedDescription; model.status = "导出未完成" }
            }
        }
    }
}

struct VideoExportProgress: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var job: VideoExportJob
    @Binding var visible: Bool
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            Text(tr(job.format=="gif" ? "导出 GIF" : "导出 MP4")).font(.title2.bold())
            if job.busy { ProgressView() }
            Text(tr(job.status)).textSelection(.enabled).lineLimit(4)
            HStack { Spacer(); Button(tr(job.busy ? "取消" : "关闭")) { job.cancel(); visible = false }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width:480).interactiveDismissDisabled(job.busy).onDisappear { job.cancel() }
    }
}

extension ViewerModel {
    func exportMP4() {
        exportVideo(format:"mp4")
    }
    func exportGIF() {
        exportVideo(format:"gif")
    }
    private func exportVideo(format: String) {
        guard package != nil, !working, animation != nil || asset?.kind == "视频" else { return }
        pause()
        let panel = NSSavePanel(); panel.title = tr(format=="gif" ? "导出 GIF" : "导出 MP4"); panel.allowedContentTypes = format=="gif" ? [.gif] : [.mpeg4Movie]; panel.canCreateDirectories = true
        panel.nameFieldStringValue = exportName(title,replaced:modified) + "." + format
        guard panel.runModal() == .OK, let target = panel.url else { return }
        var args: [String:Any] = ["kind":format,"resolution":previewResolution,"fps":Int(playbackFPS),"renderer":renderMode]
        if let animation { args["animation"] = animation.id }
        else if let asset { args["asset"] = asset.id }
        let job = VideoExportJob(); videoJob = job; showVideoExport = true
        job.start(model:self,target:target,arguments:args)
    }
}
