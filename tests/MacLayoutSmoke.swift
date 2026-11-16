import AppKit
import Foundation
import SwiftUI

@main
struct MacLayoutSmoke {
    @MainActor
    static func settle(_ view: NSView, window: NSWindow) {
        for _ in 0..<12 {
            view.layoutSubtreeIfNeeded()
            window.displayIfNeeded()
            RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.025))
        }
    }

    @MainActor
    static func main() async throws {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let model = ViewerModel()
        defer { model.backend?.stop() }
        let source = model.home.deletingLastPathComponent().appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs")
        await model.open(source.path)
        await model.selectItem()
        guard model.image != nil else { fatalError("Animation fixture failed: \(model.status)") }
        let output = model.home.appendingPathComponent("build/macos/layout")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let sizes = [NSSize(width: 1460, height: 920), NSSize(width: 1120, height: 720), NSSize(width: 1200, height: 760)]
        let fixtureImage = model.image
        let fixtureSelection = model.selectedItem
        var results: [[String: Any]] = []
        for kind in ["image", "audio", "video"] {
        for mode in kind == "image" ? PreviewScale.allCases : [.fit] {
        for size in sizes {
            model.scaleMode = mode
            model.selectedItem = kind == "image" ? fixtureSelection : nil
            model.previewKind = kind
            model.image = fixtureImage
            model.warnings = Array(repeating: "部分图层引用了外部素材；预览可能不完整，可继续查看其他素材。", count: 4)
            var frames: [String: CGRect] = [:]
            var controlFrames: [String: [CGRect]] = [:]
            let root = ViewerWindow(model: model, startAutomatically: false, layoutObserver: { frames = $0 }, controlLayoutObserver: { controlFrames = $0 })
            let hosting = NSHostingView(rootView: root.background(Color(nsColor: .windowBackgroundColor)))
            let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled, .resizable, .closable], backing: .buffered, defer: false)
            window.title = "Layout verification"
            window.isReleasedWhenClosed = false
            window.contentView = hosting
            window.setContentSize(size)
            window.orderBack(nil)
            settle(hosting, window: window)
            let modeLabel = mode == .fit ? "" : "-" + mode.rawValue.replacingOccurrences(of: ":", with: "_")
            let name = "\(kind)\(modeLabel)-\(Int(size.width))x\(Int(size.height))"
            if let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
                hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
                if let png = bitmap.representation(using: .png, properties: [:]) { try png.write(to: output.appendingPathComponent(name + ".png")) }
            }
            let playback = frames["playback"] ?? .null
            let status = frames["status"] ?? .null
            let expected = ["play", "previous", "next", "timeline", "loop", "speed"]
            let controlsFit = expected.allSatisfy { key in
                controlFrames[key]?.contains { $0.width > 0 && $0.height > 0 && playback.insetBy(dx: -1, dy: -1).contains($0) } ?? false
            }
            let fits = !playback.isNull && !status.isNull && playback.maxY <= status.minY + 1 && playback.minX >= -1 && playback.maxX <= size.width + 1 && controlsFit
            let rects = frames.mapValues { ["x": $0.minX, "y": $0.minY, "width": $0.width, "height": $0.height] }
            results.append(["size": name, "ok": fits, "controls": controlFrames.keys.sorted(), "controls_fit": controlsFit, "frames": rects])
            print("\(name): \(fits ? "PASS" : "FAIL") playback=\(playback), status=\(status), controls=\(controlFrames.keys.sorted())")
            window.close()
            window.contentView = nil
        }
        }
        }
        let data = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: output.appendingPathComponent("report.json"))
        if results.contains(where: { $0["ok"] as? Bool != true }) { exit(1) }
    }
}
