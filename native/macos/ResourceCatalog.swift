import AppKit
import Combine
import SwiftUI

final class CatalogSnapshot: @unchecked Sendable {
    let files: [ResourceFile]
    let indexesByPath: [String:Int]
    init(_ files: [ResourceFile]) {
        self.files = files; indexesByPath = Dictionary(files.enumerated().map { ($0.element.path,$0.offset) },uniquingKeysWith:{ first,_ in first })
    }
}

private actor CatalogWorker {
    private struct Row { let file: ResourceFile; let text: NSString }
    private var rows: [Row] = []
    private var previousQuery = "", previousVersion = "", previousKind = ""
    private var matches: [Int] = []
    private var snapshot = CatalogSnapshot([])

    func replace(_ files: [ResourceFile]) -> [String]? {
        var indexed: [Row] = []; indexed.reserveCapacity(files.count)
        var versions: Set<String> = []
        for (i,file) in files.enumerated() {
            if i & 255 == 0 && Task.isCancelled { return nil }
            indexed.append(Row(file:file,text:(file.relative + " " + (file.song_title ?? "")).lowercased() as NSString))
            versions.insert(file.version)
        }
        guard !Task.isCancelled else { return nil }
        rows = indexed; matches = []; snapshot = CatalogSnapshot([])
        previousQuery = ""; previousVersion = ""; previousKind = ""
        return ["全部版本"] + versions.sorted()
    }
    func search(_ query: String, version: String, kind: String) -> CatalogSnapshot? {
        let query = query.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator:" ")
        let sameFilters = version == previousVersion && kind == previousKind
        if sameFilters && query == previousQuery { return snapshot }
        let terms = query.split(separator:" ").map(String.init)
        let candidates = sameFilters && !previousQuery.isEmpty && query.hasPrefix(previousQuery) ? matches : Array(rows.indices)
        var found: [Int] = []; found.reserveCapacity(min(candidates.count,4096))
        for (offset,i) in candidates.enumerated() {
            if offset & 255 == 0 && Task.isCancelled { return nil }
            let row = rows[i]
            if (version == "全部版本" || row.file.version == version) && (kind == "所有类型" || row.file.kind == kind)
                && terms.allSatisfy({ row.text.range(of:$0,options:.literal).location != NSNotFound }) { found.append(i) }
        }
        guard !Task.isCancelled else { return nil }
        matches = found; previousQuery = query; previousVersion = version; previousKind = kind
        snapshot = CatalogSnapshot(found.map { rows[$0].file }); return snapshot
    }
}

@MainActor
final class ResourceCatalog: ObservableObject {
    @Published var query = "" { didSet { if oldValue != query { schedule() } } }
    @Published var version = "全部版本" { didSet { if oldValue != version { schedule(debounce:false) } } }
    @Published var kind = "所有类型" { didSet { if oldValue != kind { schedule(debounce:false) } } }
    @Published private(set) var results = CatalogSnapshot([])
    @Published private(set) var versions = ["全部版本"]
    @Published private(set) var count = 0
    private let worker = CatalogWorker()
    private var indexing: Task<Void,Never>?
    private var searching: Task<Void,Never>?
    private var sourceRevision = 0, revision = 0
    private var replacing = false

    func replace(_ files: [ResourceFile]) {
        sourceRevision += 1; revision += 1; let source = sourceRevision
        indexing?.cancel(); searching?.cancel(); replacing = true
        count = files.count; results = CatalogSnapshot([])
        if files.isEmpty { versions = ["全部版本"] }
        indexing = Task { [weak self, worker] in
            guard let versions = await worker.replace(files), !Task.isCancelled, let self, self.sourceRevision == source else { return }
            self.versions = versions; self.replacing = false; self.schedule(debounce:false)
        }
    }
    private func schedule(debounce: Bool = true) {
        revision += 1; searching?.cancel()
        guard !replacing else { return }
        let current = revision, query = query, version = version, kind = kind
        searching = Task { [weak self, worker] in
            if debounce { do { try await Task.sleep(for:.milliseconds(65)) } catch { return } }
            guard !Task.isCancelled, let result = await worker.search(query,version:version,kind:kind), !Task.isCancelled,
                  let self, self.revision == current else { return }
            if self.results !== result { self.results = result }
        }
    }
    func waitForResults() async {
        while true {
            let current = revision
            await indexing?.value; await searching?.value
            if current == revision && !replacing { return }
        }
    }
}

struct ResourceCatalogTable: NSViewRepresentable {
    let snapshot: CatalogSnapshot
    @Binding var selection: String?
    static func makeTable() -> NSTableView {
        let table = NSTableView()
        table.addTableColumn(NSTableColumn(identifier:NSUserInterfaceItemIdentifier("resource")))
        table.headerView = nil; table.style = .sourceList; table.rowHeight = 46
        table.intercellSpacing = NSSize(width:0,height:2); table.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        table.allowsEmptySelection = true; table.allowsMultipleSelection = false
        table.focusRingType = .none; table.backgroundColor = .clear
        table.setAccessibilityLabel(tr("资源库文件"))
        return table
    }
    func makeCoordinator() -> Coordinator { Coordinator(selection:$selection) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.drawsBackground = false
        let table = Self.makeTable(); table.dataSource = context.coordinator; table.delegate = context.coordinator
        scroll.documentView = table; return scroll
    }
    func updateNSView(_ view: NSScrollView, context: Context) {
        guard let table = view.documentView as? NSTableView else { return }
        let coordinator = context.coordinator; coordinator.selection = $selection; coordinator.updating = true
        defer { coordinator.updating = false }
        if coordinator.snapshot !== snapshot {
            coordinator.snapshot = snapshot; table.reloadData()
        }
        let row = selection.flatMap { snapshot.indexesByPath[$0] }
        if let row { if table.selectedRow != row { table.selectRowIndexes(IndexSet(integer:row),byExtendingSelection:false) } }
        else if table.selectedRow >= 0 { table.deselectAll(nil) }
    }
    final class Coordinator: NSObject, NSTableViewDataSource, NSTableViewDelegate {
        var snapshot = CatalogSnapshot([])
        var selection: Binding<String?>
        var updating = false
        init(selection: Binding<String?>) { self.selection = selection }
        func numberOfRows(in tableView: NSTableView) -> Int { snapshot.files.count }
        func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
            let identifier = NSUserInterfaceItemIdentifier("resourceCell")
            let cell = tableView.makeView(withIdentifier:identifier,owner:self) as? ResourceCell ?? ResourceCell()
            cell.identifier = identifier; cell.show(snapshot.files[row]); return cell
        }
        func tableViewSelectionDidChange(_ notification: Notification) {
            guard !updating, let table = notification.object as? NSTableView else { return }
            let current=snapshot,row=table.selectedRow
            let path=current.files.indices.contains(row) ? current.files[row].path : nil
            DispatchQueue.main.async { [weak self,weak table] in
                guard let self,let table,self.snapshot === current,table.selectedRow==row,!self.updating else { return }
                if self.selection.wrappedValue != path { self.selection.wrappedValue=path }
            }
        }
    }
}

private final class ResourceCell: NSTableCellView {
    private let icon = NSImageView(), nameLabel = NSTextField(labelWithString:""), pathLabel = NSTextField(labelWithString:""), sizeLabel = NSTextField(labelWithString:"")
    private static let symbols = ["动画/图像资源":"photo.on.rectangle.angled", "音频类文件":"waveform", "BGA":"film"].mapValues { NSImage(systemSymbolName:$0,accessibilityDescription:nil)! }
    override init(frame frameRect: NSRect) {
        super.init(frame:frameRect)
        nameLabel.font = .systemFont(ofSize:13); pathLabel.font = .systemFont(ofSize:11); sizeLabel.font = .systemFont(ofSize:10)
        nameLabel.lineBreakMode = .byTruncatingTail; pathLabel.lineBreakMode = .byTruncatingMiddle
        icon.contentTintColor = .secondaryLabelColor
        icon.widthAnchor.constraint(equalToConstant:18).isActive = true
        icon.heightAnchor.constraint(equalToConstant:18).isActive = true
        let labels = NSStackView(views:[nameLabel,pathLabel]); labels.orientation = .vertical; labels.alignment = .leading; labels.spacing = 2
        labels.setContentCompressionResistancePriority(.defaultLow,for:.horizontal)
        labels.setContentHuggingPriority(.init(1),for:.horizontal)
        sizeLabel.setContentHuggingPriority(.required,for:.horizontal)
        let row = NSStackView(views:[icon,labels,sizeLabel]); row.spacing = 8; row.alignment = .centerY; row.distribution = .fill; row.translatesAutoresizingMaskIntoConstraints = false
        addSubview(row)
        NSLayoutConstraint.activate([row.leadingAnchor.constraint(equalTo:leadingAnchor,constant:6), row.trailingAnchor.constraint(equalTo:trailingAnchor,constant:-6),row.centerYAnchor.constraint(equalTo:centerYAnchor),
            nameLabel.widthAnchor.constraint(lessThanOrEqualTo:labels.widthAnchor),pathLabel.widthAnchor.constraint(lessThanOrEqualTo:labels.widthAnchor)])
        textField = nameLabel; imageView = icon; recolor()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var backgroundStyle: NSView.BackgroundStyle { didSet { recolor() } }
    private func recolor() {
        let selected = backgroundStyle == .emphasized
        nameLabel.textColor = selected ? .selectedControlTextColor : .labelColor
        pathLabel.textColor = selected ? .selectedControlTextColor : .secondaryLabelColor
        sizeLabel.textColor = pathLabel.textColor
    }
    func show(_ file: ResourceFile) {
        nameLabel.stringValue = file.name; pathLabel.stringValue = file.relative
        sizeLabel.stringValue = ByteCountFormatter.string(fromByteCount:Int64(file.size),countStyle:.file)
        icon.image = Self.symbols[file.kind]; toolTip = file.relative
    }
}

struct ResourceCatalogSidebar: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    @ObservedObject var model: ViewerModel
    @ObservedObject var catalog: ResourceCatalog
    var body: some View {
        VStack(spacing:8) {
            HStack {
                Label(tr("资源库"),systemImage:"square.stack.3d.up").font(.headline); Spacer()
                if model.modified { Text(tr(model.draftLabel)).font(.caption).foregroundStyle(.secondary) }
            }.padding(.horizontal,12).padding(.top,12)
            TextField(tr("搜索文件、路径或乐曲名"),text:$catalog.query).textFieldStyle(.roundedBorder).padding(.horizontal,10)
            HStack {
                Picker(tr("版本"),selection:$catalog.version) { ForEach(catalog.versions,id:\.self) { Text(tr($0)) } }.labelsHidden()
                Picker(tr("类型"),selection:$catalog.kind) { ForEach(["所有类型","动画/图像资源","音频类文件","BGA"],id:\.self) { Text(tr($0)) } }.labelsHidden().frame(minWidth:125)
            }.padding(.horizontal,8)
            ResourceCatalogTable(snapshot:catalog.results,selection:$model.selectedFile)
            Text(tr("\(catalog.results.files.count.formatted()) / \(catalog.count.formatted()) 个文件")).font(.caption).foregroundStyle(.secondary).padding(.bottom,8)
        }
    }
}
