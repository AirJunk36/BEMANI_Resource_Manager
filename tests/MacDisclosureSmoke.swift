import AppKit
import Foundation
import SwiftUI

@main
struct MacDisclosureSmoke {
    @MainActor
    static func tableRows(_ view: NSView) -> Int {
        if let table = view as? NSTableView { return table.numberOfRows }
        return view.subviews.reduce(0) { $0 + tableRows($1) }
    }
    static func role(_ node: AnyObject) -> String {
        node.accessibilityRole?()?.rawValue ?? (node.accessibilityAttributeValue?(.role) as? String) ?? ""
    }
    @MainActor
    static func nodes(_ root: Any, depth: Int = 0) -> [AnyObject] {
        guard depth < 40 else { return [] }; let node = root as AnyObject
        let children = node.accessibilityChildren?() ?? (node.accessibilityAttributeValue?(.children) as? [Any]) ?? []
        return [node] + children.flatMap { nodes($0, depth: depth + 1) }
    }

    @MainActor
    static func settle(_ host: NSView, _ window: NSWindow) async throws {
        for _ in 0..<12 {
            host.layoutSubtreeIfNeeded(); window.displayIfNeeded()
            RunLoop.main.run(mode: .default, before: Date(timeIntervalSinceNow: 0.02))
            try await Task.sleep(nanoseconds: 30_000_000)
        }
    }

    @MainActor
    static func press(_ host: NSView, id: String? = nil, label: String? = nil) {
        guard let button = nodes(host).first(where: { node in
            if let id { return node.accessibilityIdentifier?() == id || node.accessibilityAttributeValue?(.identifier) as? String == id }
            return node.accessibilityRole?() == .button && node.accessibilityLabel?() == label
        }) else {
            for node in nodes(host) { print("AX", type(of: node), node.accessibilityRole?()?.rawValue ?? "", node.accessibilityIdentifier?() ?? "", node.accessibilityLabel?() ?? "") }
            fflush(stdout)
            fatalError("Missing disclosure button: \(id ?? label ?? "")")
        }
        print("Press", id ?? label ?? "", type(of: button), "enabled", button.isAccessibilityEnabled?() as Any); fflush(stdout)
        if let control = button as? NSButton { control.performClick(nil) }
        else if let cell = button as? NSButtonCell, let control = cell.controlView as? NSButton { control.performClick(nil) }
        else if button.accessibilityPerformPress?() != true { fatalError("Disclosure action failed") }
    }

    @MainActor
    static func main() async throws {
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        app.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXManualAccessibility"))
        app.accessibilitySetValue(true, forAttribute: .init(rawValue: "AXEnhancedUserInterface"))
        let model = ViewerModel(); defer { model.backend?.stop() }
        await model.open(model.home.deletingLastPathComponent().appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs").path)
        await model.selectItem()
        guard let animation = model.animation else { fatalError(model.status) }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1460, height: 920), styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let browser = NSHostingView(rootView: ViewerWindow(model: model, startAutomatically: false))
        window.contentView = browser; window.orderFront(nil); try await settle(browser, window)
        let rows = nodes(browser).filter { role($0) == "AXRow" }.count
        press(browser, id: "category.动画"); try await settle(browser, window)
        guard nodes(browser).filter({ role($0) == "AXRow" }).count < rows, model.animation?.id == animation.id, model.image != nil else { fatalError("Category collapse lost preview or kept rows") }
        press(browser, id: "category.动画"); try await settle(browser, window)
        guard nodes(browser).filter({ role($0) == "AXRow" }).count == rows else { fatalError("Category expansion did not restore rows") }
        print("PASS: macOS category collapse/expand through native accessibility buttons preserves preview")
        let panel = NSHostingView(rootView: AnimationLayersPanel(animationID: animation.id, selectedID: nil,
            load: { try await model.animationLayers(animation.id) },
            update: { id, visible, packageID in try await model.setLayer(id, visible: visible, animationID: animation.id, packageID: packageID) }))
        window.contentView = panel; window.setContentSize(NSSize(width: 440, height: 500)); try await settle(panel, window)
        let boxes = tableRows(panel)
        guard boxes > 1 else { fatalError("Material list failed to load") }
        press(panel, label: "全部收起"); try await settle(panel, window)
        if let bitmap = panel.bitmapImageRepForCachingDisplay(in: panel.bounds) {
            panel.cacheDisplay(in: panel.bounds, to: bitmap)
            try bitmap.representation(using: .png, properties: [:])?.write(to: model.home.appendingPathComponent("build/picking/mac-collapsed.png"))
        }
        print("Material row count before/after collapse:", boxes, tableRows(panel)); fflush(stdout)
        guard tableRows(panel) == 1 else { fatalError("Material groups did not collapse") }
        press(panel, id: "layer.fold.root"); try await settle(panel, window)
        guard tableRows(panel) > 1 else { fatalError("Root group did not expand") }
        press(panel, label: "全部展开"); try await settle(panel, window)
        guard tableRows(panel) == boxes else { fatalError("Material groups did not restore") }
        print("PASS: macOS material groups and all expand/collapse through native buttons")
        window.close()
    }
}
