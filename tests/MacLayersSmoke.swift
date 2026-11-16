import AppKit
import Foundation

@main
struct MacLayersSmoke {
    @MainActor
    static func main() async throws {
        let model = ViewerModel()
        defer { model.backend?.stop() }
        let viewport = CGSize(width: 900, height: 700), sourceSize = CGSize(width: 640, height: 480)
        for mode in PreviewScale.allCases {
            let size = mode.size(sourceSize, in: viewport)
            for zoom in [0.5, 1.0, 2.0] {
                let offset = CGSize(width: 51, height: -24)
                let point = CGPoint(x: viewport.width / 2 + offset.width + size.width * zoom * 0.25,
                                    y: viewport.height / 2 + offset.height - size.height * zoom * 0.25)
                let normalized = PreviewGeometry.normalized(point, viewport: viewport, image: size, zoom: zoom, offset: offset)
                guard abs(normalized.x - 0.75) < 1e-9, abs(normalized.y - 0.25) < 1e-9 else { fatalError("Zoom/pan/aspect picking coordinates") }
            }
        }
        for relative in ["20_tricoro/data/graphic/test_effect_anime.ifs", "14_GOLD/data/graph/sys/0200/owl.bin"] {
            let source = model.home.deletingLastPathComponent().appendingPathComponent(relative)
            let before = try Data(contentsOf: source)
            await model.open(source.path); await model.selectItem(); await model.render(3)
            guard let animation = model.animation, model.image != nil else { fatalError("Animation preview: \(model.status)") }
            let catalog = try await model.animationLayers(animation.id)
            guard catalog.layers.count > 1 else { fatalError("No animation materials") }
            _ = try await model.setLayer("root", visible: false, animationID: animation.id, packageID: catalog.package_id)
            await model.render(3)
            for _ in 0..<500 { if !model.renderBusy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
            guard let data = model.image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data),
                  bitmap.colorAt(x: bitmap.pixelsWide / 2, y: bitmap.pixelsHigh / 2)?.alphaComponent == 0 else { fatalError("Hidden image is not transparent") }
            let restored = try await model.setLayer("root", visible: true, animationID: animation.id, packageID: catalog.package_id)
            guard restored.layers.allSatisfy({ $0.visible }) else { fatalError("Restore failed") }
            await model.render(3)
            for _ in 0..<500 { if !model.renderBusy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
            guard let restoredData = model.image?.tiffRepresentation, let pixels = NSBitmapImageRep(data: restoredData) else { fatalError("Restored bitmap") }
            var point: CGPoint?
            for y in stride(from: 0, to: pixels.pixelsHigh, by: max(1, pixels.pixelsHigh / 50)) {
                for x in stride(from: 0, to: pixels.pixelsWide, by: max(1, pixels.pixelsWide / 50)) {
                    if (pixels.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.5 {
                        point = CGPoint(x: (Double(x) + 0.5) / Double(pixels.pixelsWide), y: (Double(y) + 0.5) / Double(pixels.pixelsHigh)); break
                    }
                }
                if point != nil { break }
            }
            guard let point else { fatalError("No visible material pixel") }
            model.togglePlay()
            await model.pickLayer(at: point)
            guard !model.isPlaying, let chosen = model.selectedLayerID, model.showLayers,
                  model.selectedLayerBounds?.insetBy(dx: -0.01, dy: -0.01).contains(point) == true,
                  catalog.layers.contains(where: { $0.id == chosen }) else { fatalError("Preview pick did not select visible material: \(model.status)") }
            _ = try await model.setLayer(chosen, visible: false, animationID: animation.id, packageID: catalog.package_id)
            guard model.selectedLayerID == nil, model.selectedLayerBounds == nil else { fatalError("Hidden selection remained") }
            _ = try await model.setLayer("root", visible: true, animationID: animation.id, packageID: catalog.package_id)
            await model.pickLayer(at: CGPoint(x: -1, y: -1))
            guard model.selectedLayerID == nil, model.selectedLayerBounds == nil, !model.showLayers else { fatalError("Empty preview did not deselect") }
            model.togglePlay(); try await Task.sleep(nanoseconds: 120_000_000); model.tick()
            try await Task.sleep(nanoseconds: 60_000_000); model.pause()
            guard model.image != nil, try Data(contentsOf: source) == before else { fatalError("Playback/source protection failed") }
            print("PASS: \(relative), \(catalog.layers.count) material controls, pick/highlight/pause/deselect, hide/restore/play; source unchanged")
        }
    }
}
