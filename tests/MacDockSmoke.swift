import AppKit

@main
struct MacDockSmoke {
    @MainActor
    static func main() throws {
        let home = URL(fileURLWithPath: CommandLine.arguments[1])
        let output = home.appendingPathComponent("build/macos/dock")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let bundle = Bundle(url: home.appendingPathComponent("BEMANI素材管理器.app"))!
        let expected = NSWorkspace.shared.icon(forFile: bundle.bundlePath)
        ViewerApplicationDelegate.applyDockIcon(bundle: bundle)
        let actual = NSApplication.shared.applicationIconImage!
        func pixels(_ image: NSImage) -> NSBitmapImageRep {
            let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 256, pixelsHigh: 256,
                bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                colorSpaceName: .deviceRGB, bytesPerRow: 1024, bitsPerPixel: 32)!
            bitmap.bitmapData!.initialize(repeating: 0, count: 256 * 1024)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
            image.draw(in: NSRect(x: 0, y: 0, width: 256, height: 256))
            NSGraphicsContext.restoreGraphicsState()
            return bitmap
        }
        let rendered = pixels(actual)
        let wanted = pixels(expected)
        try rendered.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent("dock-running.png"))
        try wanted.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent("dock-launch.png"))
        // AppKit may select a different ICNS resolution for the Dock image.
        let count = 256 * 1024
        var error = 0
        for i in 0..<count { error += abs(Int(rendered.bitmapData![i]) - Int(wanted.bitmapData![i])) }
        let meanError = Double(error) / Double(count)
        guard meanError < 3 else {
            fatalError("Running Dock icon differs from the system launch icon")
        }
        print("PASS: launch and running Dock icons match; resampling error \(meanError) / 255")
    }
}
