import AppKit
import SwiftUI

struct LiveParameter: Decodable, Identifiable {
    let id: Int
    let key, name: String
    let minimum, maximum, value: Double
    let `default`: Double
    let common: Bool
}
struct LiveRow: Decodable, Identifiable, Hashable { let id: Int; let name: String }
struct LiveInfo: Decodable {
    let version: Int
    let parameters: [LiveParameter]
    let sections: [String: [LiveRow]]
    let textures, documents: [String]
    let revision: Int
    let can_undo, can_redo: Bool
}

@MainActor
final class LiveEditorState: ObservableObject {
    let model: ViewerModel
    let source: String
    @Published var info: LiveInfo?
    @Published var details: [String: Any] = [:]
    @Published var points: [CGPoint] = []
    @Published var triangles: [[Int]] = []
    @Published var section = "meshes"
    @Published var selected = 0
    @Published var form = 0
    @Published var vertex = 0
    @Published var uv = false
    @Published var document = ""
    @Published var json = ""
    @Published var message = ""
    @Published var busy = false
    @Published var values: [Int: Double] = [:]
    var defaults: [Int: Double] = [:]
    private var sliderTask: Task<Void, Never>?
    private var queuedEdit: [String: Any]?
    private var detailGeneration = 0
    private var pending: [String: Any]?
    private var editing = false

    init(_ model: ViewerModel) { self.model = model; source = model.package?.path ?? "" }
    var valid: Bool { source == model.package?.path }
    var record: [String: Any] { details["record"] as? [String: Any] ?? [:] }
    var owner: [String: Any] { details["owner"] as? [String: Any] ?? [:] }
    var formRecord: [String: Any] { details["form_record"] as? [String: Any] ?? [:] }
    var formCount: Int { max(1, details["form_count"] as? Int ?? 1) }
    var editSection: String { details["section"] as? String ?? section }
    var editIndex: Int { details["index"] as? Int ?? selected }

    func load() async {
        do {
            info = try await model.decodedRequest(LiveInfo.self, "live2d_info")
            values = Dictionary(uniqueKeysWithValues: (info?.parameters ?? []).map { ($0.id, $0.value) })
            defaults = Dictionary(uniqueKeysWithValues: (info?.parameters ?? []).map { ($0.id, $0.default) })
            document = info?.documents.first(where: { $0.hasSuffix(".motion3.json") }) ?? info?.documents.first ?? ""
        } catch { message = error.localizedDescription }
    }
    func flush() async { await sliderTask?.value; while editing { try? await Task.sleep(for:.milliseconds(15)) } }
    func pick(_ point:CGPoint) async -> Int? {
        guard let animation=model.animation else {return nil}
        do {
            let reply=try await model.request("pick_layer",["animation":animation.id,"frame":Int(model.frame),"u":point.x,"v":point.y,"renderer":model.renderMode])
            return (reply["selected"] as? String)?.split(separator:":").last.flatMap {Int($0)}
        } catch {message=error.localizedDescription;return nil}
    }
    func previewForm() async {
        model.pause();await flush()
        await edit(["operation":"pose","keys":details["keys"] as? [[String:Any]] ?? []])
        values=Dictionary(uniqueKeysWithValues:(info?.parameters ?? []).map {($0.id,$0.value)})
    }
    func detail() async {
        detailGeneration += 1; let generation = detailGeneration
        do {
            let result = try await model.request("live2d_details", ["section": uv && section == "meshes" ? "uvs" : section, "index": selected, "form": form])
            guard generation == detailGeneration, valid else { return }
            details = result
            points = (result["points"] as? [[Double]] ?? []).map { CGPoint(x: $0[0], y: $0[1]) }
            triangles = result["triangles"] as? [[Int]] ?? []
            vertex = min(vertex, max(0, points.count - 1)); message = ""
        } catch { if generation == detailGeneration { message = error.localizedDescription } }
    }
    func edit(_ arguments: [String: Any], refresh: Bool = true) async {
        guard valid else { return }
        if editing { pending = arguments; return }
        editing = true; busy = true
        do {
            info = try await model.decodedRequest(LiveInfo.self, "live2d_edit", arguments)
            if refresh { await detail() }
            let package = try await model.decodedRequest(PackageInfo.self, "info")
            if valid { model.package = package; await model.render(Int(model.frame)) }
            message = ""
        } catch { message = error.localizedDescription }
        editing = false; busy = false
        if let next = pending { pending = nil; await edit(next, refresh: refresh) }
    }
    func parameter(_ id: Int, _ value: Double) {
        values[id] = value
        queue(["operation": "parameter", "index": id, "value": value])
    }
    func queue(_ arguments: [String: Any]) {
        queuedEdit=arguments
        guard sliderTask==nil else { return }
        sliderTask = Task {
            while queuedEdit != nil {
                do { try await Task.sleep(for: .milliseconds(60)); try Task.checkCancellation() } catch { return }
                if let args=queuedEdit { queuedEdit=nil;await edit(args,refresh:false) }
            }
            sliderTask=nil
        }
    }
    func drag(_ id:Int,_ point:CGPoint) {
        guard points.indices.contains(id) else { return }
        points[id]=point
        queue(["operation":"point","section":editSection,"index":editIndex,"form":form,"vertex":id,"x":point.x,"y":point.y])
    }
    func move(_ id: Int, _ point: CGPoint) async {
        guard points.indices.contains(id) else { return }
        points[id] = point
        await edit(["operation": "point", "section": editSection, "index": editIndex, "form": form, "vertex": id, "x": point.x, "y": point.y])
    }
    func field(_ name: String, _ value: Double, keyform: Bool = false) async {
        await edit(["operation": "field", "section": keyform ? details["form_section"] as? String ?? editSection : editSection,
                    "index": keyform ? details["form_index"] as? Int ?? editIndex : editIndex, "field": name, "value": value])
    }
    func loadJSON() async {
        guard !document.isEmpty else { return }
        do { let result = try await model.request("live2d_details", ["section": "json", "document": document]); json = result["text"] as? String ?? "" }
        catch { message = error.localizedDescription }
    }
    func texture(_ index: Int) {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.png, .jpeg, .tiff]
        if panel.runModal() == .OK, let url = panel.url {
            Task { await edit(["operation": "texture", "index": index, "path": url.path], refresh: false) }
        }
    }
    func save() {
        let panel = NSSavePanel(); panel.title = tr("导出模型"); panel.nameFieldStringValue = URL(fileURLWithPath: source).deletingPathExtension().deletingPathExtension().lastPathComponent + "-edited"
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                do {
                    await sliderTask?.value
                    let result = try await model.request("live2d_save", ["target": url.path])
                    message = "已导出"; if let path = result["path"] as? String { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
                } catch { message = error.localizedDescription }
            }
        }
    }
    func undo(_ redo: Bool) async {
        await sliderTask?.value; await edit(["operation": redo ? "redo" : "undo"])
        values = Dictionary(uniqueKeysWithValues: (info?.parameters ?? []).map { ($0.id, $0.value) })
        await loadJSON()
    }
}

struct Live2DEditor: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @StateObject private var editor: LiveEditorState
    @Environment(\.dismiss) private var dismiss
    @AppStorage("live2d.professional") private var professional = false
    @State private var tab = "参数"
    @State private var query = ""
    @State private var pickedMesh: Int?
    init(model: ViewerModel) { self.model = model; _editor = StateObject(wrappedValue: LiveEditorState(model)) }
    private var tabs: [String] { professional ? ["参数", "贴图", "网格", "变形器", "动作/物理"] : ["参数", "贴图"] }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(tr("Live2D 编辑")).font(.title2.bold())
                Picker(tr("编辑模式"), selection: $professional) { Text(tr("简易模式")).tag(false); Text(tr("专业模式")).tag(true) }.pickerStyle(.segmented).frame(width: 230)
                Spacer()
                Button { Task { await editor.undo(false) } } label: { Image(systemName: "arrow.uturn.backward") }.help(tr("撤销")).disabled(editor.info?.can_undo != true || editor.busy)
                Button { Task { await editor.undo(true) } } label: { Image(systemName: "arrow.uturn.forward") }.help(tr("重做")).disabled(editor.info?.can_redo != true || editor.busy)
                Button(tr("导出模型…"), action: editor.save).disabled(editor.busy)
                Button(tr("完成")) { model.pause(); Task { await editor.flush(); dismiss() } }.keyboardShortcut(.cancelAction)
            }.padding(16)
            Divider()
            HSplitView {
                VStack(spacing: 10) {
                    LiveEditorPreview(preview: model.preview) { point in
                        guard professional else { return }
                        Task {
                            if let index = await editor.pick(point) {
                                if tab != "网格" {pickedMesh=index;tab="网格"}
                                else {editor.selected=index;editor.form=0;editor.uv=false;await editor.detail()}
                            }
                        }
                    }
                    HStack {
                        Picker(tr("动作"), selection: $model.selectedItem) {
                            ForEach(model.package?.animations ?? []) { Text(tr($0.name)).tag(Optional("a:\($0.id)")) }
                        }.frame(maxWidth: 260)
                        Button { model.togglePlay() } label: { Image(systemName: model.isPlaying ? "pause.fill" : "play.fill") }.frame(width: 28)
                        Toggle(tr("循环"), isOn: $model.loop)
                        Button(tr("跟随动作")) {Task {await editor.flush();await editor.edit(["operation":"release"],refresh:false)}}.help(tr("取消预览参数的固定值"))
                    }
                    LiveEditorTimeline(model: model, preview: model.preview)
                }.padding(12).frame(minWidth: 380, idealWidth: 570)
                VStack(alignment: .leading, spacing: 12) {
                    Picker(tr("工具"), selection: $tab) { ForEach(tabs, id: \.self) { Text(tr($0)) } }.pickerStyle(.segmented)
                    if tab == "参数" { parameters }
                    else if tab == "贴图" { textures }
                    else if tab == "动作/物理" { documents }
                    else { geometry }
                }.padding(14).frame(minWidth: 410, idealWidth: 510, maxWidth: 600)
            }
            Divider()
            HStack {
                if editor.busy { ProgressView().controlSize(.small) }
                Text(tr(editor.message.isEmpty ? "修改后导出为新模型" : editor.message)).lineLimit(2).textSelection(.enabled)
                Spacer()
            }.font(.caption).padding(12)
        }.frame(minWidth: 1040, idealWidth: 1180, minHeight: 700, idealHeight: 820)
            .task { await editor.load() }
            .onChange(of: professional) { _, value in if !value { tab = "参数" } }
            .onChange(of: tab) { _, value in
                query = ""
                if value == "网格" || value == "变形器" {
                    editor.section = value == "网格" ? "meshes" : "deformers"; editor.selected = pickedMesh ?? 0; pickedMesh=nil; editor.form = 0; editor.uv = false
                    Task { await editor.detail() }
                } else if value == "动作/物理" { Task { await editor.loadJSON() } }
            }
    }
    private var parameters: some View {
        VStack {
            if professional { TextField(tr("搜索参数"), text: $query).textFieldStyle(.roundedBorder) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    let all = editor.info?.parameters ?? []
                    let common = all.filter(\.common)
                    let rows = professional ? all.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || $0.key.localizedCaseInsensitiveContains(query) } : Array((common.isEmpty ? all : common).prefix(18))
                    ForEach(rows) { p in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack { Text(tr(p.name)).lineLimit(1).help(tr(p.key)); Spacer(); Button(tr("重置")) { editor.parameter(p.id, editor.defaults[p.id] ?? p.default) }.controlSize(.mini) }
                            HStack {
                                Slider(value: Binding(get: { editor.values[p.id] ?? p.value }, set: { editor.parameter(p.id, $0) }), in: p.minimum...max(p.minimum + 0.00001, p.maximum))
                                Text(tr((editor.values[p.id] ?? p.value).formatted(.number.precision(.fractionLength(2))))).monospacedDigit().frame(width: 60, alignment: .trailing)
                            }
                            if professional {
                                DisclosureGroup("范围") {
                                    HStack {
                                        LiveNumber(label: "最小", value: p.minimum) { await editor.edit(["operation":"field", "section":"parameters", "index":p.id, "field":"minimum", "value":$0], refresh:false) }
                                        LiveNumber(label: "最大", value: p.maximum) { await editor.edit(["operation":"field", "section":"parameters", "index":p.id, "field":"maximum", "value":$0], refresh:false) }
                                    }
                                }.font(.caption)
                            }
                        }
                    }
                }.padding(4)
            }
        }
    }
    private var textures: some View {
        List(Array((editor.info?.textures ?? []).enumerated()), id: \.offset) { index, name in
            HStack {
                Text(tr(name)).lineLimit(2); Spacer(); Button(tr("替换…")) { editor.texture(index) }
            }.padding(.vertical, 8)
        }
    }
    private var documents: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker(tr("配置"), selection: $editor.document) { ForEach(editor.info?.documents.filter { !$0.hasSuffix(".model3.json") } ?? [], id: \.self) { Text(tr($0)) } }
                .onChange(of: editor.document) { Task { await editor.loadJSON() } }
            TextEditor(text: $editor.json).font(.system(.caption, design: .monospaced)).scrollContentBackground(.hidden).border(.quaternary)
            HStack { Button(tr("重新载入")) { Task { await editor.loadJSON() } }; Spacer(); Button(tr("应用")) { Task { await editor.edit(["operation":"json", "document":editor.document, "text":editor.json], refresh:false) } }.disabled(editor.busy) }
        }
    }
    private var geometry: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(tr("搜索"), text: $query).textFieldStyle(.roundedBorder)
            Picker(tr("元素"), selection: $editor.selected) {
                ForEach((editor.info?.sections[editor.section] ?? []).filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { Text(tr($0.name)).tag($0.id) }
            }.onChange(of: editor.selected) { editor.form = 0; Task { await editor.detail() } }
            HStack {
                Stepper("关键形 \(editor.form + 1) / \(editor.formCount)", value: $editor.form, in: 0...max(0, editor.formCount - 1)).onChange(of: editor.form) { Task { await editor.detail() } }
                if editor.section == "meshes" { Toggle(tr("UV"), isOn: $editor.uv).onChange(of: editor.uv) { Task { await editor.detail() } } }
            }
            if let keys=editor.details["keys"] as? [[String:Any]],!keys.isEmpty {
                HStack {
                    Text(tr(keys.map {"\($0["key"] as? String ?? "") = \(String(format:"%.3g",$0["value"] as? Double ?? 0))"}.joined(separator:" · "))).font(.caption).lineLimit(2).help(tr("当前关键形的参数值"))
                    Spacer();Button(tr("预览关键形")) {Task {await editor.previewForm()}}
                }
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if !editor.points.isEmpty {
                        LivePointCanvas(points: editor.points, triangles: editor.triangles, selected: $editor.vertex,
                                        rows: editor.record["rows"] as? Int ?? 0, columns: editor.record["columns"] as? Int ?? 0,
                                        drag:editor.drag) { id, point in Task { await editor.flush(); await editor.move(id, point) } }
                            .frame(height: 230)
                        HStack {
                            Stepper("顶点 \(editor.vertex + 1)", value: $editor.vertex, in: 0...max(0,editor.points.count - 1))
                            if editor.points.indices.contains(editor.vertex) {
                                let point = editor.points[editor.vertex]
                                LiveNumber(label: "X", value: point.x) { await editor.move(editor.vertex, CGPoint(x:$0,y:point.y)) }
                                LiveNumber(label: "Y", value: point.y) { await editor.move(editor.vertex, CGPoint(x:point.x,y:$0)) }
                            }
                        }
                        LiveTransformTools { dx,dy,scale,angle in await editor.edit(["operation":"transform", "section":editor.editSection,"index":editor.editIndex,"form":editor.form,"dx":dx,"dy":dy,"scale":scale,"angle":angle]) }
                        if !editor.uv {
                            HStack {
                                LiveNumber(label: "不透明度", value: editor.formRecord["opacity"] as? Double ?? 1) { await editor.field("opacity",$0,keyform:true) }
                                if editor.section == "meshes" { LiveNumber(label: "绘制顺序", value: editor.formRecord["order"] as? Double ?? 0) { await editor.field("order",$0,keyform:true) } }
                            }
                        }
                        if editor.section == "meshes" && !editor.uv { LiveTriangleTools(triangles:editor.triangles) { triangle,vertices in await editor.edit(["operation":"triangle","index":editor.selected,"triangle":triangle,"vertices":vertices]) } }
                    } else if editor.editSection == "rotation_forms" {
                        ForEach([("angle","角度"),("x","X"),("y","Y"),("scale","缩放"),("opacity","不透明度")], id: \.0) { field,label in
                            LiveNumber(label:label,value:editor.record[field] as? Double ?? 0) { await editor.field(field,$0) }
                        }
                    }
                    Picker(tr("父变形器"), selection: Binding(get: { editor.owner["parent"] as? Int ?? -1 }, set: { parent in Task { await editor.edit(["operation":"parent","section":editor.section,"index":editor.selected,"parent":parent]) } })) {
                        Text(tr("无")).tag(-1)
                        ForEach(editor.info?.sections["deformers"] ?? []) { row in if editor.section != "deformers" || row.id != editor.selected { Text(tr(row.name)).tag(row.id) } }
                    }
                    if editor.section == "meshes" {
                        Picker(tr("贴图"),selection:Binding(get:{editor.owner["texture"] as? Int ?? 0},set:{index in Task {await editor.edit(["operation":"field","section":"meshes","index":editor.selected,"field":"texture","value":index])}})) {
                            ForEach(Array((editor.info?.textures ?? []).enumerated()),id:\.offset) {index,name in Text(tr(name)).tag(index)}
                        }
                        Picker(tr("混合"),selection:Binding(get:{(editor.owner["flags"] as? Int ?? 0)&3},set:{mode in Task {await editor.edit(["operation":"field","section":"meshes","index":editor.selected,"field":"flags","value":((editor.owner["flags"] as? Int ?? 0)&12)|mode])}})) {
                            Text(tr("正常")).tag(0);Text(tr("加法")).tag(1);Text(tr("正片叠底")).tag(2)
                        }
                        Toggle(tr("反向剪裁"),isOn:Binding(get:{((editor.owner["flags"] as? Int ?? 0)&8) != 0},set:{value in Task {await editor.edit(["operation":"field","section":"meshes","index":editor.selected,"field":"flags","value":((editor.owner["flags"] as? Int ?? 0)&7)|(value ? 8 : 0)])}}))
                        Toggle(tr("显示"), isOn: Binding(get:{ (editor.owner["visible"] as? Int ?? 1) != 0 },set:{value in Task { await editor.edit(["operation":"field","section":"meshes","index":editor.selected,"field":"visible","value":value ? 1 : 0]) }}))
                    }
                    Text(tr("编辑现有网格与变形器的关键形")).font(.caption).foregroundStyle(.secondary)
                }.padding(4)
            }
        }
    }
}

private struct LiveNumber: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let label: String
    let value: Double
    let apply: (Double) async -> Void
    @State private var draft = ""
    var body: some View {
        HStack(spacing: 5) {
            Text(tr(label)).font(.caption)
            TextField(tr(label), text: $draft).textFieldStyle(.roundedBorder).frame(minWidth: 60)
                .onSubmit { if let number = Double(draft), number.isFinite { Task { await apply(number) } } }
        }.onAppear { sync() }.onChange(of: value) { sync() }
    }
    private func sync() { draft = String(format:"%.5g",value) }
}
private struct LiveTransformTools: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let apply: (Double,Double,Double,Double) async -> Void
    @State private var dx = 0.0
    @State private var dy = 0.0
    @State private var scale = 1.0
    @State private var angle = 0.0
    var body: some View {
        DisclosureGroup("变换") {
            HStack { TextField(tr("X"),value:$dx,format:.number); TextField(tr("Y"),value:$dy,format:.number) }.textFieldStyle(.roundedBorder)
            HStack { TextField(tr("缩放"),value:$scale,format:.number); TextField(tr("旋转"),value:$angle,format:.number); Button(tr("应用")) { Task { await apply(dx,dy,scale,angle) } } }.textFieldStyle(.roundedBorder)
        }
    }
}
private struct LiveTriangleTools: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let triangles: [[Int]]
    let apply: (Int,[Int]) async -> Void
    @State private var selected = 0
    @State private var indices = ""
    var body: some View {
        DisclosureGroup("三角形") {
            Stepper("三角形 \(selected + 1)",value:$selected,in:0...max(0,triangles.count - 1)).onChange(of:selected) { sync() }
            HStack { TextField(tr("顶点编号（从 1 开始）"),text:$indices).textFieldStyle(.roundedBorder); Button(tr("应用")) { let values = indices.split(whereSeparator: { $0 == "," || $0 == " " }).compactMap { Int($0).map { $0 - 1 } }; Task { await apply(selected,values) } } }
        }.onAppear { sync() }.onChange(of:triangles) { sync() }
    }
    private func sync() { if triangles.indices.contains(selected) { indices = triangles[selected].map { String($0 + 1) }.joined(separator:", ") } }
}

private struct LivePointCanvas: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let points: [CGPoint]
    let triangles: [[Int]]
    @Binding var selected: Int
    let rows, columns: Int
    let drag: (Int,CGPoint) -> Void
    let move: (Int,CGPoint) -> Void
    @State private var dragged: CGPoint?
    @State private var dragIndex: Int?
    @State private var dragBounds: CGRect?
    private var bounds: CGRect {
        guard let first = points.first else { return CGRect(x:0,y:0,width:1,height:1) }
        var x0=first.x,x1=first.x,y0=first.y,y1=first.y
        for p in points { x0=min(x0,p.x);x1=max(x1,p.x);y0=min(y0,p.y);y1=max(y1,p.y) }
        let extent=max(0.01,max(x1-x0,y1-y0))*1.2
        return CGRect(x:(x0+x1-extent)/2,y:(y0+y1-extent)/2,width:extent,height:extent)
    }
    var body: some View {
        GeometryReader { geometry in
            let box=dragBounds ?? bounds
            let w=max(1,geometry.size.width),h=max(1,geometry.size.height)
            let scale=min(w,h)/box.width
            let screen: (CGPoint)->CGPoint = { p in CGPoint(x:w/2+(p.x-box.midX)*scale,y:h/2-(p.y-box.midY)*scale) }
            Canvas { context,size in
                context.fill(Path(CGRect(origin:.zero,size:size)),with:.color(.black.opacity(0.07)))
                var path=Path()
                if !triangles.isEmpty {
                    for t in triangles where t.count==3 && t.allSatisfy({ points.indices.contains($0) }) {
                        path.move(to:screen(points[t[0]]));path.addLine(to:screen(points[t[1]]));path.addLine(to:screen(points[t[2]]));path.closeSubpath()
                    }
                } else if rows>0 && columns>0 && (rows+1)*(columns+1)==points.count {
                    for y in 0...rows { for x in 0...columns {
                        let i=y*(columns+1)+x
                        if x<columns {path.move(to:screen(points[i]));path.addLine(to:screen(points[i+1]))}
                        if y<rows {path.move(to:screen(points[i]));path.addLine(to:screen(points[i+columns+1]))}
                    } }
                }
                context.stroke(path,with:.color(.secondary.opacity(0.6)),lineWidth:0.7)
                for i in points.indices {
                    let p=screen(i==dragIndex ? dragged ?? points[i] : points[i]);let r:CGFloat=i==selected ? 4 : 2.5
                    context.fill(Path(ellipseIn:CGRect(x:p.x-r,y:p.y-r,width:r*2,height:r*2)),with:.color(i==selected ? .orange : .accentColor))
                }
            }.gesture(DragGesture(minimumDistance:0).onChanged { event in
                if dragIndex==nil {
                    guard let nearest=points.indices.min(by:{ hypot(screen(points[$0]).x-event.startLocation.x,screen(points[$0]).y-event.startLocation.y)<hypot(screen(points[$1]).x-event.startLocation.x,screen(points[$1]).y-event.startLocation.y) }),hypot(screen(points[nearest]).x-event.startLocation.x,screen(points[nearest]).y-event.startLocation.y)<18 else {return}
                    dragIndex=nearest;selected=nearest;dragBounds=box
                }
                dragged=CGPoint(x:box.midX+(event.location.x-w/2)/scale,y:box.midY-(event.location.y-h/2)/scale)
                if let id=dragIndex,let point=dragged {drag(id,point)}
            }.onEnded { _ in
                if let id=dragIndex,let point=dragged {move(id,point)}
                dragIndex=nil;dragged=nil;dragBounds=nil
            })
        }.clipShape(RoundedRectangle(cornerRadius:8))
    }
}
private struct LiveEditorPreview: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var preview: PreviewState
    let pick: (CGPoint)->Void
    var body: some View {
        GeometryReader { viewport in
            ZStack {
                Color(nsColor:.underPageBackgroundColor)
                if let image=preview.image {
                    let size=PreviewScale.fit.size(image.size,in:viewport.size)
                    Image(nsImage:image).resizable().frame(width:size.width,height:size.height)
                        .gesture(SpatialTapGesture().onEnded { event in pick(CGPoint(x:event.location.x/size.width,y:event.location.y/size.height)) })
                }
            }.frame(maxWidth:.infinity,maxHeight:.infinity)
        }.clipShape(RoundedRectangle(cornerRadius:10))
    }
}
private struct LiveEditorTimeline: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @ObservedObject var preview: PreviewState
    var body: some View {
        Slider(value:Binding(get:{preview.timelineFrame},set:{value in model.pause();Task { await model.render(Int(value)) }}),in:0...Double(max(1,(model.animation?.length ?? 2)-1)))
    }
}
