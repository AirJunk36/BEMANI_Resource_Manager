import AppKit
import Combine
import Foundation

@main
struct MacPerformanceSmoke {
    @MainActor static func main() throws {
        let model = ViewerModel()
        model.files = (0..<20000).map { ResourceFile(path:"/test/\($0).ifs",relative:"data/graphic/\($0).ifs",name:"\($0).ifs",version:"33_test",kind:"动画/图像资源",size:1024) }
        var notifications = 0
        let subscription = model.objectWillChange.sink { notifications += 1 }
        let image = NSImage(size:NSSize(width:64,height:48))
        let start = ProcessInfo.processInfo.systemUptime
        for i in 0..<1000 {
            model.image = image; model.timelineFrame = Double(i); model.mediaTime = Double(i)/60; model.mediaDuration = 120
        }
        let result: [String:Any] = ["frame_updates":1000,"library_files":20000,"library_model_notifications":notifications,
                                  "elapsed_ms":(ProcessInfo.processInfo.systemUptime-start)*1000]
        print(String(data:try JSONSerialization.data(withJSONObject:result,options:.sortedKeys),encoding:.utf8)!)
        withExtendedLifetime(subscription) { }
        if CommandLine.arguments.contains("--expect-isolated") && notifications != 0 { fatalError("播放更新仍在刷新资源库") }
    }
}
