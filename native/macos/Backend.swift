import Foundation
import AppKit
import Darwin

func exportName(_ title: String, fallback: String = "asset", replaced: Bool = false) -> String {
    let folded=englishExportTitle(title).folding(options:.diacriticInsensitive,locale:Locale(identifier:"en_US_POSIX"))
    var name=String(folded.unicodeScalars.filter { $0.isASCII }).replacingOccurrences(of:#"[^A-Za-z0-9._ -]+"#,with:"_",options:.regularExpression).trimmingCharacters(in:CharacterSet(charactersIn:". _"))
    if name.isEmpty { name=fallback.unicodeScalars.allSatisfy({$0.isASCII}) ? fallback : "asset" }
    if replaced && !name.hasSuffix("_replaced") { name+="_replaced" }
    return name
}

struct BackendError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private final class ResponseReader: @unchecked Sendable {
    private let queue = DispatchQueue(label: "local.iidx.response-decoder", qos: .userInitiated)
    private var bytes = Data()
    func append(_ chunk: Data, deliver: @escaping ([String: Any]) -> Void) {
        queue.async { [self] in
            bytes.append(chunk)
            while let end = bytes.firstIndex(of: 10) {
                let line = Data(bytes[..<end])
                bytes.removeSubrange(...end)
                if let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { deliver(object) }
            }
        }
    }
}

@MainActor
final class Backend {
    let process = Process()
    let input = Pipe()
    let output = Pipe()
    let cache: URL
    private let background: Bool
    private let reader = ResponseReader()
    var serial = 0
    var pending: [Int: CheckedContinuation<[String: Any], Error>] = [:]

    init(home: URL, background: Bool = false) throws {
        self.background = background
        cache = FileManager.default.temporaryDirectory.appendingPathComponent("iidx-swiftui-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        let candidates = [Bundle.main.resourceURL?.appendingPathComponent("Core"),
                          home.appendingPathComponent("BEMANI素材管理器.app/Contents/Resources/Core")].compactMap { $0 }
        let embedded = candidates.first { FileManager.default.fileExists(atPath: $0.appendingPathComponent("backend.py").path) }
        let core = embedded ?? home
        let python = embedded == nil ? home.appendingPathComponent(".venv/bin/python") : core.appendingPathComponent("bin/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw BackendError(message: "应用不完整，请重新获取完整版本。")
        }
        process.executableURL = python
        process.arguments = [core.appendingPathComponent("backend.py").path, "--cache", cache.path]
        process.currentDirectoryURL = core
        var environment = ProcessInfo.processInfo.environment
        if background { environment["IIDX_BACKGROUND_WORKER"] = "1" }
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["PYTHONUNBUFFERED"] = "1"
        if embedded != nil {
            environment["PYTHONHOME"] = core.appendingPathComponent("Python.framework/Versions/Current").path
            environment["PYTHONPATH"] = core.appendingPathComponent("site-packages").path + ":" + core.path
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONSAFEPATH"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            environment["QT_PLUGIN_PATH"] = core.appendingPathComponent("site-packages/PySide6/Qt/plugins").path
            environment.removeValue(forKey: "QT_QPA_PLATFORM_PLUGIN_PATH")
            environment.removeValue(forKey: "VIRTUAL_ENV")
            let media = core.appendingPathComponent("site-packages/imageio_ffmpeg/binaries")
            if let executable = try? FileManager.default.contentsOfDirectory(at: media, includingPropertiesForKeys: nil).first(where: { $0.lastPathComponent.hasPrefix("ffmpeg-macos-") }) {
                environment["IMAGEIO_FFMPEG_EXE"] = executable.path
            }
        }
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        let log = cache.appendingPathComponent("backend.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        process.standardError = try FileHandle(forWritingTo: log)
        let reader = self.reader
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            reader.append(chunk) { object in
                DispatchQueue.main.async { self?.receive(object) }
            }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let logText = (try? String(contentsOf: log, encoding: .utf8)) ?? ""
                let message = "解析核心已退出（\(process.terminationStatus)）。\n" + String(logText.suffix(1800))
                for continuation in self.pending.values {
                    continuation.resume(throwing: BackendError(message: message))
                }
                self.pending.removeAll()
            }
        }
        try process.run()
    }

    func receive(_ object: [String: Any]) {
            guard let id = object["id"] as? Int, let continuation = pending.removeValue(forKey: id) else { return }
            if object["ok"] as? Bool == true {
                continuation.resume(returning: object["result"] as? [String: Any] ?? [:])
            } else {
                continuation.resume(throwing: BackendError(message: object["error"] as? String ?? "资源解析失败"))
            }
    }

    func decoded<T: Decodable>(_ type: T.Type, _ method: String, _ arguments: [String: Any] = [:]) async throws -> T {
        let reply = try await request(method, arguments)
        return try await Task.detached(priority: .userInitiated) { try decode(type, reply) }.value
    }

    func request(_ method: String, _ arguments: [String: Any] = [:]) async throws -> [String: Any] {
        guard process.isRunning else { throw BackendError(message: "解析核心已退出，请重新打开应用。") }
        serial += 1
        let id = serial
        var message = arguments
        message["id"] = id
        message["method"] = method
        var data = try JSONSerialization.data(withJSONObject: message)
        data.append(10)
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do { try input.fileHandleForWriting.write(contentsOf: data) }
            catch { pending.removeValue(forKey: id)?.resume(throwing: error) }
        }
    }

    func stop() {
        output.fileHandleForReading.readabilityHandler = nil
        try? input.fileHandleForWriting.close()
        if process.isRunning {
            if !background || Darwin.kill(-process.processIdentifier, SIGTERM) != 0 { process.terminate() }
            process.waitUntilExit()
        }
        try? FileManager.default.removeItem(at: cache)
    }
}

func decode<T: Decodable>(_ type: T.Type, _ object: [String: Any]) throws -> T {
    try JSONDecoder().decode(type, from: JSONSerialization.data(withJSONObject: object))
}

struct ResourceFile: Decodable, Identifiable, Sendable {
    var id: String { path }
    let path: String
    let relative: String
    let name: String
    let version: String
    let kind: String
    let size: Int
    var song_title: String? = nil
}
struct ScanResult: Decodable { let root: String; let files: [ResourceFile] }
struct AnimationItem: Decodable, Identifiable {
    let id: Int
    let name: String
    let length: Int
    let fps: Double
    let width: Int
    let height: Int
    let warnings: [String]
}
struct AssetItem: Decodable, Identifiable {
    let id: Int
    let name: String
    let kind: String
    let size: Int
    let details: String
    let replaceable: Bool?
}
struct PackageInfo: Decodable {
    let path: String
    var assets: [AssetItem]
    let animations: [AnimationItem]
    let warnings: [String]
    let modified: Bool?
    let change_count: Int?
    let can_undo: Bool?
    let selected_asset: String?
    let edit_notice: String?
    let engine: EngineInfo?
    var format: String? = nil
    var song_preview: Bool? = nil
    var song_title: String? = nil
    var song_artist: String? = nil
    var displayTitle: String {
        let title=[URL(fileURLWithPath:path).lastPathComponent,song_title ?? "",song_artist ?? ""].filter { !$0.isEmpty }.joined(separator:" · ")
        return modified==true ? exportName(title,fallback:"resource",replaced:true) : title
    }
}

struct EngineInfo: Decodable {
    let family: String
    let library: GameLibraryInfo?
    var label: String { (family == "Live2D" || family == "27+") ? "27代+/SDVX6+" : family == "AP2" ? "通用动画" : "\(family.replacingOccurrences(of: "-", with: "–")) 代" }
    var detail: String { library.map { URL(fileURLWithPath: $0.path).lastPathComponent + " · " + $0.architecture + ($0.version.map { " · " + $0 } ?? "") } ?? "按格式和目录识别" }
}
struct GameLibraryInfo: Decodable { let path: String; let architecture: String; let version: String? }
struct PreviewInfo: Decodable {
    let kind: String
    let path: String?
    let frame: Int?
    let text: String?
    let warnings: [String]?
    let assets: [AssetItem]?
    let package: PackageInfo?
    let width: Int?
    let height: Int?
    let stride: Int?
    let offset: Int?
    let pixel_format: String?
    let renderer: RendererInfo?
}

struct RendererInfo: Decodable, Equatable {
    let hardware: Bool
    let api: String?
    let device: String?
    let reason: String?
}

final class RawFrameReader {
    private var path = ""
    private var mapped: UnsafeMutableRawPointer?
    private var mappedCount = 0

    deinit { close() }

    private func close() {
        if let mapped { munmap(mapped, mappedCount) }
        mapped = nil
        mappedCount = 0
        path = ""
    }

    private func map(_ file: String) throws {
        close()
        let descriptor = Darwin.open(file, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { throw BackendError(message: "无法打开预览帧缓存") }
        defer { Darwin.close(descriptor) }
        var attributes = stat()
        guard fstat(descriptor, &attributes) == 0, attributes.st_size > 0,
              attributes.st_size <= Int.max else { throw BackendError(message: "预览帧缓存大小无效") }
        let count = Int(attributes.st_size)
        guard let address = mmap(nil, count, PROT_READ, MAP_SHARED, descriptor, 0),
              address != MAP_FAILED else { throw BackendError(message: "无法映射预览帧缓存") }
        mapped = address
        mappedCount = count
        path = file
    }

    func image(_ frame: PreviewInfo) throws -> NSImage {
        guard frame.pixel_format == "BGRA8_PREMULTIPLIED", let file = frame.path,
              let width = frame.width, let height = frame.height, let stride = frame.stride,
              let offset = frame.offset, width > 0, height > 0, width <= 1920, height <= 1920,
              width * height <= 1920 * 1080,
              stride == width * 4, offset >= 0 else { throw BackendError(message: "预览帧格式无效") }
        if path != file || mapped == nil {
            // Foundation Data can retain a snapshot even with .alwaysMapped.
            // The producer rewrites this file, so use an explicit shared mapping.
            try map(file)
        }
        let count = stride * height
        guard let mapped, count <= mappedCount, offset <= mappedCount - count else { throw BackendError(message: "预览帧超出缓存范围") }
        // Copy the selected ring slot so a later frame cannot change a displayed image.
        let pixels = Data(bytes: mapped.advanced(by: offset), count: count)
        guard let provider = CGDataProvider(data: pixels as CFData), let bitmap = CGImage(width: width, height: height,
            bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: stride, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedFirst.rawValue).union(.byteOrder32Little),
            provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) else {
            throw BackendError(message: "无法创建预览图像")
        }
        return NSImage(cgImage: bitmap, size: NSSize(width: width, height: height))
    }
}
