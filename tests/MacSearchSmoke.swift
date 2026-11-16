import AppKit
import Foundation

@main
struct MacSearchSmoke {
    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.accessory)
        let model = ViewerModel(); defer { model.backend?.stop() }
        model.files = (0..<100000).map { i in
            ResourceFile(path:"/test/\(i).ifs",relative:"33_test/data/\(i % 4 == 0 ? "movie" : "graphic")/\(i % 5 == 0 ? "title" : "notes")_\(i).ifs",name:"\(i).ifs",version:"33_test",kind:i % 4 == 0 ? "BGA" : "动画/图像资源",size:1024,
                song_title:i % 7 == 0 ? "Ascalon アスカロン 乐曲名称 Generic BGA" : "Another Song 別の楽曲")
        }
        var times: [Double] = []
        for query in ["a","as","asc","asca","ascal","ascalo","ascalon","title","tit","ti","t",""] {
            let begin = ProcessInfo.processInfo.systemUptime
            model.search = query
            _ = model.filteredFiles.count
            times.append((ProcessInfo.processInfo.systemUptime-begin)*1000)
        }
        let result: [String:Any] = ["files":model.files.count,"input_and_result_read_ms":times,"max_main_thread_ms":times.max() ?? 0]
        print(String(data:try JSONSerialization.data(withJSONObject:result,options:.sortedKeys),encoding:.utf8)!)
        #if !LEGACY_SEARCH
        let resultStart = ProcessInfo.processInfo.systemUptime
        model.search = "Ascalon"; await model.library.waitForResults()
        print("First indexed result ms: \((ProcessInfo.processInfo.systemUptime-resultStart)*1000)")
        guard model.filteredFiles.count == 14286 else { fatalError("曲名搜索结果错误") }
        model.search = "not-present"; model.search = "TITLE"; model.kind = "BGA"; await model.library.waitForResults()
        guard model.filteredFiles.count == 5000 else { fatalError("未采用最新搜索和类型筛选") }
        model.version = "missing"; await model.library.waitForResults()
        guard model.filteredFiles.isEmpty else { fatalError("版本筛选失败") }
        model.version = "全部版本"; model.kind = "所有类型"; model.search = ""; await model.library.waitForResults()
        guard model.filteredFiles.count == 100000 else { fatalError("清空搜索丢失文件") }
        let table = ResourceCatalogTable.makeTable()
        let scroll = NSScrollView(frame:NSRect(x:0,y:0,width:330,height:500)); scroll.documentView = table
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:330,height:500),styleMask:[.titled],backing:.buffered,defer:false)
        window.isReleasedWhenClosed = false; window.contentView = scroll; window.orderBack(nil)
        let coordinator = ResourceCatalogTable.Coordinator(selection:.constant(nil)); table.dataSource = coordinator; table.delegate = coordinator
        coordinator.snapshot = model.library.results; table.reloadData(); table.layoutSubtreeIfNeeded(); window.displayIfNeeded()
        try await Task.sleep(for:.milliseconds(80)); window.displayIfNeeded()
        guard table.numberOfRows == 100000, table.view(atColumn:0,row:0,makeIfNecessary:false) != nil,
              table.view(atColumn:0,row:99999,makeIfNecessary:false) == nil else { fatalError("资源列表未按可见行加载") }
        window.close()
        print("Latest query, song names, category/version filters and 100,000-row virtualization passed")
        #endif
    }
}
