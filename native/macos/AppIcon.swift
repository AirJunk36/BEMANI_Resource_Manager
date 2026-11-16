import AppKit

@MainActor
final class ViewerApplicationDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        Self.applyDockIcon()
    }

    static func applyDockIcon(bundle: Bundle = .main) {
        guard bundle.url(forResource: "Application", withExtension: "icns") != nil else { return }
        // Match IconServices' launch icon, including the current macOS treatment.
        let icon = NSWorkspace.shared.icon(forFile: bundle.bundlePath)
        icon.size = NSSize(width: 512, height: 512)
        NSApplication.shared.applicationIconImage = icon
    }
}
