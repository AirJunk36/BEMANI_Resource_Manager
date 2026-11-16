import AppKit
import Foundation

// Compare decoded pixels, rather than only checking that an NSImage was made.
func canonicalPixels(_ image: NSImage) -> [UInt8] {
    let bitmap = image.cgImage(forProposedRect: nil, context: nil, hints: nil)!
    var bytes = [UInt8](repeating: 0, count: bitmap.width * bitmap.height * 4)
    bytes.withUnsafeMutableBytes { buffer in
        let context = CGContext(data: buffer.baseAddress, width: bitmap.width, height: bitmap.height,
            bitsPerComponent: 8, bytesPerRow: bitmap.width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
        context.draw(bitmap, in: CGRect(x: 0, y: 0, width: bitmap.width, height: bitmap.height))
    }
    return bytes
}

@main
struct MacFrameSmoke {
    @MainActor
    static func main() async throws {
        let args = CommandLine.arguments
        guard let flag = args.firstIndex(of: "--home"), flag + 1 < args.count else {
            fatalError("Pass --home with the software directory")
        }
        let home = URL(fileURLWithPath: args[flag + 1])
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("iidx-frame-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let path = folder.appendingPathComponent("frames")
        try Data(count: 256 * 3).write(to: path)
        let writer = try FileHandle(forWritingTo: path)
        defer { try? writer.close() }
        let reader = RawFrameReader()
        var first: NSImage?
        var firstPixels: [UInt8] = []
        for frame in 0..<12 {
            let sizes = [(4, 3), (9, 2), (3, 6)]
            let variant = (frame + frame / 3) % 3
            let (width, height) = sizes[variant]
            let color: [UInt8] = variant == 0 ? [0, 0, 255, 255] : variant == 1 ? [0, 255, 0, 255] : [255, 0, 0, 255]
            let bytes = Data((0..<(width * height)).flatMap { _ in color })
            let offset = (frame % 3) * 256
            try writer.seek(toOffset: UInt64(offset))
            try writer.write(contentsOf: bytes)
            let info = try decode(PreviewInfo.self, ["kind": "raw", "path": path.path,
                "width": width, "height": height, "stride": width * 4, "offset": offset,
                "pixel_format": "BGRA8_PREMULTIPLIED"])
            let image = try reader.image(info)
            let actual = canonicalPixels(image)
            let expected = [color[2], color[1], color[0], color[3]]
            guard actual.count == width * height * 4,
                  stride(from: 0, to: actual.count, by: 4).allSatisfy({ Array(actual[$0..<$0+4]) == expected }) else {
                fatalError("Shared frame is stale or has incorrect channels/dimensions: \(frame)")
            }
            if frame == 0 { first = image; firstPixels = actual }
        }
        guard canonicalPixels(first!) == firstPixels else { fatalError("A displayed frame changed after ring reuse") }
        print("PASS: 12 shared frames, three-slot wrap, changing dimensions, BGRA channels and immutable displayed image")

        let backend = try Backend(home: home)
        defer { backend.stop() }
        let nativeReader = RawFrameReader()
        var checked = 0
        for (relative, frames) in [
            ("20_tricoro/data/graphic/test_effect_anime.ifs", [0, 3, 5, 8, 1, 4]),
            ("18_Resort_Anthem/data/graph_data/sys/0200.bin", [0, 3, 5, 8, 1, 4]),
            ("33_Sparkle_Shower/data/graphic/0/mselect.ifs", [0, 3, 5, 8, 1, 4]),
            ("31_EPOLIS/contents/data/graphic/0/title.ifs", [0, 3, 8, 321])
        ] {
            let source = home.deletingLastPathComponent().appendingPathComponent(relative)
            if !FileManager.default.fileExists(atPath: source.path) { continue }
            let package = try decode(PackageInfo.self, await backend.request("open", ["path": source.path]))
            let animation = package.animations[0]
            for (index, desired) in frames.enumerated() {
                let frame = min(animation.length - 1, desired)
                let settings: [String: Any] = ["animation": animation.id, "frame": frame,
                    "resolution": index % 3 == 0 ? 1080 : 720, "renderer": index % 2 == 0 ? "gpu" : "cpu"]
                let raw = try decode(PreviewInfo.self, await backend.request("render", settings.merging(["transport": "shared"]) { _,new in new }))
                let image = try nativeReader.image(raw)
                let reference = try decode(PreviewInfo.self, await backend.request("render", settings))
                guard let png = reference.path.flatMap({ NSImage(contentsOfFile: $0) }),
                      canonicalPixels(image) == canonicalPixels(png) else {
                    fatalError("Native shared preview differs from PNG: \(relative), frame \(frame)")
                }
                checked += 1
                print("PASS: \(relative), frame \(frame), native/PNG pixel equality")
            }
        }
        guard checked > 0 else { fatalError("No real animation fixtures found") }
        print("PASS: \(checked) actual animation frames across file and size changes")
    }
}
