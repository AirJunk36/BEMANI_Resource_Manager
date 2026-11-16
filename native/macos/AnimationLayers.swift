import SwiftUI

struct AnimationLayer: Decodable, Identifiable {
    let id: String
    let parent: String
    let name: String
    let detail: String
    let level: Int
    let visible: Bool
}

struct AnimationLayersReply: Decodable {
    let layers: [AnimationLayer]
    let truncated: Bool
    let package_id: Int
    let selected: String?
    let bounds: [Double]?
}

// Local state keeps playback frame updates out of the searchable layer list.
struct AnimationLayersPanel: View, Equatable {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let animationID: Int
    let selectedID: String?
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.animationID == rhs.animationID && lhs.selectedID == rhs.selectedID }
    let load: () async throws -> AnimationLayersReply
    let update: (String, Bool, Int) async throws -> AnimationLayersReply

    var body: some View { AnimationLayerList(selectedID: selectedID, load: load, update: update) }
}

private struct AnimationLayerList: View {
    @ObservedObject private var appLanguage = AppLanguage.shared
    let selectedID: String?
    let load: () async throws -> AnimationLayersReply
    let update: (String, Bool, Int) async throws -> AnimationLayersReply
    @State private var reply: AnimationLayersReply?
    @State private var search = ""
    @State private var busy = false
    @State private var error = ""
    @State private var collapsed: Set<String> = []

    private var groups: Set<String> { Set((reply?.layers ?? []).map(\.parent).filter { !$0.isEmpty }) }
    private var visibleRows: [AnimationLayer] {
        let closed = collapsed
        let rows = reply?.layers ?? []
        if !search.isEmpty { return rows.filter { $0.name.localizedCaseInsensitiveContains(search) || $0.detail.localizedCaseInsensitiveContains(search) } }
        return rows.filter { row in
            var parent = row.parent
            while !parent.isEmpty {
                if closed.contains(parent) { return false }
                parent = parent.contains("/") ? String(parent[..<parent.lastIndex(of: "/")!]) : ""
            }
            return true
        }
    }

    private func revealSelection() {
        var key = selectedID ?? ""
        while !key.isEmpty { collapsed.remove(key); key = key.contains("/") ? String(key[..<key.lastIndex(of: "/")!]) : "" }
    }

    private func refresh(_ operation: @escaping () async throws -> AnimationLayersReply) {
        busy = true
        Task {
            defer { busy = false }
            do {
                let firstLoad = reply == nil
                reply = try await operation()
                if firstLoad { revealSelection() }
                error = ""
            }
            catch { self.error = error.localizedDescription }
        }
    }

    private func set(_ id: String, _ shown: Bool) {
        guard let reply else { return }
        refresh { try await update(id, shown, reply.package_id) }
    }

    var body: some View {
        let groupIDs = groups
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(tr("素材列表")).font(.headline)
                Spacer()
                if busy { ProgressView().controlSize(.small) }
                Button(tr("刷新")) { refresh(load) }
            }
            TextField(tr("搜索素材"), text: $search).textFieldStyle(.roundedBorder)
            HStack {
                Button(tr("全显示")) { set("root", true) }
                Button(tr("全隐藏")) { set("root", false) }
                Spacer()
                Text(tr("\(reply?.layers.count ?? 0) 项")).foregroundStyle(.secondary)
            }
            HStack {
                Button(tr("全部展开")) { collapsed.removeAll() }
                Button(tr("全部收起")) { collapsed = groups }
            }
            ScrollViewReader { proxy in
            List {
                ForEach(visibleRows) { layer in
                  HStack(spacing: 5) {
                    if groupIDs.contains(layer.id) {
                        Button { if !collapsed.insert(layer.id).inserted { collapsed.remove(layer.id) } } label: {
                            Image(systemName: collapsed.contains(layer.id) ? "chevron.right" : "chevron.down").frame(width: 12)
                        }.buttonStyle(.plain).help(tr(collapsed.contains(layer.id) ? "展开" : "收起"))
                            .accessibilityLabel(tr(collapsed.contains(layer.id) ? "展开分组" : "收起分组")).accessibilityIdentifier("layer.fold.\(layer.id)")
                    } else { Color.clear.frame(width: 12, height: 1) }
                    Toggle(isOn: Binding(get: { layer.visible }, set: { set(layer.id, $0) })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(tr(layer.name)).lineLimit(1)
                            Text(tr(layer.detail)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }.toggleStyle(.checkbox).help(tr(layer.id))
                  }.padding(.leading, CGFloat(min(layer.level, 8) * 12))
                    .listRowBackground(layer.id == selectedID ? Color.accentColor.opacity(0.18) : Color.clear).id(layer.id)
                }
            }.listStyle(.inset)
                .onChange(of: busy) { _, value in if !value, let selectedID { proxy.scrollTo(selectedID, anchor: .center) } }
                .onChange(of: selectedID) { search = ""; revealSelection(); if let selectedID { proxy.scrollTo(selectedID, anchor: .center) } }
            }
            if reply?.truncated == true { Text(tr("列表已截断；分组开关仍控制全部子素材")).font(.caption).foregroundStyle(.secondary) }
            if !error.isEmpty { Text(tr(error)).font(.caption).foregroundStyle(.red) }
        }.padding(14).frame(width: 440, height: 500).disabled(busy).task { refresh(load) }
    }
}
