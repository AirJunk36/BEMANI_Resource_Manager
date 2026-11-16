import AppKit
import AVFoundation
import Foundation

@main
struct MacModelSmoke {
    @MainActor
    static func main() async throws {
        let model = ViewerModel()
        defer { model.backend?.stop() }
        await model.startup()
        guard model.files.isEmpty, model.root.isEmpty, model.package == nil,
              model.image == nil, model.previewKind == "empty", !model.working else {
            fatalError("普通启动不应加载资源：\(model.status)")
        }
        print("Default startup: no files loaded")
        let appBundle = Bundle(url: model.home.appendingPathComponent("BEMANI素材管理器.app"))!
        ViewerApplicationDelegate.applyDockIcon(bundle: appBundle)
        let dockIcon = NSApplication.shared.applicationIconImage!
        guard let dockBitmap = NSBitmapImageRep(data: dockIcon.tiffRepresentation!),
              dockBitmap.pixelsWide > 0, dockBitmap.pixelsHigh > 0 else { fatalError("Dock icon is missing") }
        print("Dock icon: explicit AppKit application icon applied")
        model.files = (0..<20000).map { index in
            let name = index % 5 == 0 ? "title_\(index).ifs" : "notes_\(index).ifs"
            return ResourceFile(path: "/test/\(name)", relative: "33_test/data/graphic/\(name)", name: name, version: "33_test", kind: "动画/图像资源", size: 1024)
        }
        model.search = "TITLE"
        await model.library.waitForResults()
        guard model.filteredFiles.count == 4000, model.versions == ["全部版本", "33_test"] else { fatalError("资源库搜索缓存错误") }
        model.kind = "BGA"
        await model.library.waitForResults()
        guard model.filteredFiles.isEmpty else { fatalError("资源库类型筛选缓存未失效") }
        model.kind = "所有类型"
        model.version = "missing"
        await model.library.waitForResults()
        guard model.filteredFiles.isEmpty else { fatalError("资源库版本筛选缓存未失效") }
        model.version = "全部版本"
        await model.library.waitForResults()
        let begin = ProcessInfo.processInfo.systemUptime
        for _ in 0..<120 { guard model.filteredFiles.count == 4000 else { fatalError("资源库缓存结果变化") } }
        print("Library cache: 20,000 rows / 120 reads in \((ProcessInfo.processInfo.systemUptime-begin)*1000) ms")
        model.files = []
        await model.library.waitForResults()
        guard model.filteredFiles.isEmpty, model.versions == ["全部版本"] else { fatalError("资源库更换后缓存未失效") }
        model.search = ""
        let original = NSSize(width: 400, height: 800), viewport = NSSize(width: 1000, height: 500)
        guard PreviewScale.actual.size(original, in: viewport) == original,
              PreviewScale.fit.size(original, in: viewport) == NSSize(width: 250, height: 500),
              PreviewScale.stretch.size(original, in: viewport) == viewport else { fatalError("预览比例模式错误") }
        for (mode,ratio) in [(PreviewScale.widescreen,16.0/9),(PreviewScale.standard,4.0/3)] {
            let size = mode.size(original, in: viewport)
            guard abs(size.width/size.height-ratio)<0.001, size.width<=viewport.width, size.height<=viewport.height else { fatalError("固定显示比例错误") }
        }
        print("Display modes: actual pixels, 16:9, 4:3, fit and stretch passed")
        let source = model.home.deletingLastPathComponent().appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs")
        await model.open(source.path)
        guard model.package != nil, model.animation != nil else {
            fatalError("打开 IFS 失败：\(model.status)")
        }
        await model.selectItem()
        guard model.image != nil else { fatalError("未收到原生图片预览：\(model.status)") }
        guard model.renderer?.hardware == true else { fatalError("未使用硬件 GPU：\(model.renderer?.reason ?? model.status)") }
        print("Hardware renderer: \(model.renderer?.api ?? "?") / \(model.renderer?.device ?? "?"); shared BGRA frame transport")
        await model.render(3)
        guard model.frame == 3 else { fatalError("逐帧定位失败") }
        let layerAnimation = model.animation!.id
        let layerState = try await model.animationLayers(layerAnimation)
        guard layerState.layers.count > 1 else { fatalError("Animation layer list is empty") }
        let hidden = try await model.setLayer("root", visible: false, animationID: layerAnimation, packageID: layerState.package_id)
        guard hidden.layers.allSatisfy({ !$0.visible }) else { fatalError("Hide all layers failed") }
        for _ in 0..<500 { if !model.renderBusy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        let shown = try await model.setLayer("root", visible: true, animationID: layerAnimation, packageID: layerState.package_id)
        guard shown.layers.allSatisfy({ $0.visible }) else { fatalError("Restore layers failed") }
        for _ in 0..<500 { if !model.renderBusy { break }; try await Task.sleep(nanoseconds: 10_000_000) }
        print("Animation layers: list, hide, restore and native redraw passed")
        model.togglePlay()
        try await Task.sleep(nanoseconds: 20_000_000)
        model.tick()
        try await Task.sleep(nanoseconds: 80_000_000)
        model.pause()
        guard model.image != nil, model.previewKind == "image" else { fatalError("动画播放未产生图像") }
        print("SwiftUI model + Python bridge: opened \(model.package!.animations.count) animations, rendered frame \(Int(model.frame)), native NSImage \(model.image!.size)")
        // Drive the real playback clock at a known elapsed time on a 30 FPS source.
        model.selectedItem = "a:\(model.package!.animations.first { abs($0.fps - 30) < 0.01 && $0.length > 8 }!.id)"
        await model.selectItem()
        model.speed = 1; model.baseFrame = 0
        model.startTime = ProcessInfo.processInfo.systemUptime - 0.101
        model.isPlaying = true; model.tick(); model.pause()
        for _ in 0..<500 {
            if !model.renderBusy && model.frame >= 6 { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        guard model.frame >= 6, model.frame <= 7 else { fatalError("默认播放没有按 60 FPS 推进：\(model.frame)") }
        print("Default animation playback: 30 FPS source advances \(Int(model.frame)) frames in 0.1 seconds at 60 FPS")
        model.playbackFPS = 120; model.baseFrame = 0
        model.startTime = ProcessInfo.processInfo.systemUptime - 0.051
        model.isPlaying = true; model.tick(); model.pause()
        for _ in 0..<500 {
            if !model.renderBusy { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        guard model.frame >= 6, model.frame <= 7 else { fatalError("120 FPS 播放时钟错误：\(model.frame)") }
        model.playbackFPS = 60
        model.renderMode = "cpu"; await model.render(3)
        guard model.renderer?.hardware == false, model.renderer?.api == "CPU" else { fatalError("CPU 渲染切换失败") }
        model.renderMode = "gpu"; await model.render(3)
        guard model.renderer?.hardware == true else { fatalError("GPU 渲染切换失败") }
        print("Preview settings: 120 FPS clock and CPU/GPU switching passed")
        model.seekFrame(1); model.seekFrame(4); model.seekFrame(8)
        for _ in 0..<500 {
            if !model.renderBusy && model.frame == 8 { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        guard model.frame == 8, model.timelineFrame == 8 else { fatalError("快速定位未保留最后一帧") }
        print("Rapid seek: latest requested frame wins")
        let legacy = [
            "14_GOLD/data/graph/sys/0200/owl.bin",
            "16_EMPRESS/data/graph/sys/0200/osprey.bin",
            "18_Resort_Anthem/data/graph_data/sys/0200.bin",
            "19_Lincle_cn/contents/modules/data/graphic/0200.bin"
        ]
        for relative in legacy {
            let path = model.home.deletingLastPathComponent().appendingPathComponent(relative)
            await model.open(path.path)
            guard model.animation != nil else { fatalError("旧版 BIN 未读取到动画：\(relative) / \(model.status)") }
            await model.selectItem()
            await model.render(3)
            guard model.image != nil, model.frame == 3 else { fatalError("旧版 BIN 预览失败：\(relative) / \(model.status)") }
            print("Legacy BIN native preview: \(relative), \(model.package!.animations.count) animations")
        }
        let editedSource = model.home.deletingLastPathComponent().appendingPathComponent("20_tricoro/data/graphic/notes00.ifs")
        let before = try Data(contentsOf: editedSource)
        let fixture = FileManager.default.temporaryDirectory.appendingPathComponent("iidx-mac-replace-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: fixture, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: fixture) }
        let png = fixture.appendingPathComponent("replacement.png")
        let drawing = NSImage(size: NSSize(width: 64, height: 64))
        drawing.lockFocus()
        NSColor.systemRed.setFill(); NSBezierPath(rect: NSRect(x: 0, y: 0, width: 64, height: 64)).fill()
        drawing.unlockFocus()
        try NSBitmapImageRep(data: drawing.tiffRepresentation!)!.representation(using: .png, properties: [:])!.write(to: png)
        await model.open(editedSource.path)
        await model.selectItem()
        guard model.canReplaceAsset, model.image != nil else { fatalError("图片替换入口不可用") }
        let broken = fixture.appendingPathComponent("broken.png")
        try Data("not an image".utf8).write(to: broken)
        await model.replace(with: broken)
        guard !model.modified, model.image != nil else { fatalError("替换失败破坏了原预览") }
        await model.replace(with: png)
        await model.selectItem()
        guard model.modified, model.canUndoReplacement, model.image != nil else { fatalError("替换副本未进入原生预览：\(model.status)") }
        let saved = fixture.appendingPathComponent("saved")
        _ = try await model.request("save_replacement", ["target": saved.path])
        guard FileManager.default.fileExists(atPath: saved.appendingPathComponent("notes00.ifs").path) else { fatalError("另存结果缺失") }
        model.undoReplacement()
        for _ in 0..<500 {
            if !model.working { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        await model.selectItem()
        guard !model.modified, model.image != nil, try Data(contentsOf: editedSource) == before else { fatalError("撤销/源文件保护失败") }
        print("Native replacement: failed edit keeps preview; image replacement/save/undo passed; source unchanged")
        let fixtures = Process()
        fixtures.executableURL = model.backend?.process.executableURL
        fixtures.environment = model.backend?.process.environment
        fixtures.arguments = ["-c", """
        from pathlib import Path
        import subprocess, sys, wave, math, struct
        import imageio_ffmpeg
        folder = Path(sys.argv[1])
        with wave.open(str(folder / 'replacement.wav'), 'wb') as stream:
            stream.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
            stream.writeframes(b''.join(struct.pack('<h', int(10000 * math.sin(2 * math.pi * 440 * i / 22050))) for i in range(11025)))
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x64:r=24', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', '-n', str(folder / 'replacement.mp4')], check=True)
        """, fixture.path]
        fixtures.currentDirectoryURL = model.home
        try fixtures.run(); fixtures.waitUntilExit()
        guard fixtures.terminationStatus == 0 else { fatalError("音视频替换测试素材生成失败") }
        model.player.isMuted = true
        for bank in ["0101/01011.2dx", "0110/0110.2dx", "0109/0109.2dx", "0100/01001.2dx"] {
            let original = model.home.deletingLastPathComponent().appendingPathComponent("12_HAPPY_SKY/data/sd_data/" + bank)
            let originalBytes = try Data(contentsOf: original)
            await model.open(original.path)
            await model.selectItem()
            for _ in 0..<500 {
                if model.player.currentItem?.status == .readyToPlay { break }
                try await Task.sleep(nanoseconds: 20_000_000)
            }
            guard model.previewKind == "audio", model.player.currentItem?.status == .readyToPlay else { fatalError("Legacy audio preview: \(model.status)") }
            model.togglePlay()
            for _ in 0..<150 {
                try await Task.sleep(nanoseconds: 20_000_000)
                model.tick()
                if model.mediaTime > 0 { break }
            }
            model.pause()
            guard model.mediaTime > 0, try Data(contentsOf: original) == originalBytes else { fatalError("Legacy audio playback/source protection") }
        }
        print("Legacy encrypted audio: all 9th–12th schemes play in AVPlayer; sources unchanged")
        for (relative, replacement, kind) in [
            ("20_tricoro/data/sound/system/syssd_category_voice_lv1.sd9", "replacement.wav", "audio"),
            ("20_tricoro/data/movie/20red_decide.wmv", "replacement.mp4", "video")
        ] {
            let original = model.home.deletingLastPathComponent().appendingPathComponent(relative)
            let originalBytes = try Data(contentsOf: original)
            await model.open(original.path)
            await model.selectItem()
            guard model.canReplaceAsset else { fatalError("音视频替换入口不可用：\(relative)") }
            await model.replace(with: fixture.appendingPathComponent(replacement))
            await model.selectItem()
            for _ in 0..<500 {
                if model.player.currentItem?.status == .readyToPlay { break }
                try await Task.sleep(nanoseconds: 20_000_000)
            }
            guard model.modified, model.previewKind == kind, model.player.currentItem?.status == .readyToPlay else {
                fatalError("音视频替换后无法原生预览：\(relative) / \(model.status)")
            }
            model.tick()
            guard model.mediaDuration > 0, model.mediaDuration < 2 else { fatalError("替换后媒体时长不符") }
            model.togglePlay()
            for _ in 0..<150 {
                try await Task.sleep(nanoseconds: 20_000_000)
                model.tick()
                if model.mediaTime > 0 { break }
            }
            model.tick(); model.pause()
            guard model.mediaTime > 0, try Data(contentsOf: original) == originalBytes else { fatalError("替换后播放/源文件保护失败") }
            print("Native replacement \(kind): AVPlayer duration \(model.mediaDuration), playback \(model.mediaTime); source unchanged")
        }
        let inspected = model.home.deletingLastPathComponent().appendingPathComponent("33_Sparkle_Shower/data/graphic/0/title.ifs")
        let inspectedBytes = try Data(contentsOf: inspected)
        await model.open(inspected.path); await model.selectItem()
        model.inspectAnimation()
        for _ in 0..<1400 {
            if !model.working { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        guard model.animation?.name == "title", model.showInspection, model.inspectionText.contains("游戏用途：标题画面"),
              model.inspectionText.contains("DLL 读取根时间轴"), model.inspectionText.contains("depth "),
              try Data(contentsOf: inspected) == inspectedBytes else {
            fatalError("DLL 动画信息读取失败：\(model.status)；动画=\(model.animation?.name ?? "nil")；详情=\(model.showInspection)；忙碌=\(model.working)\n\(model.inspectionText.prefix(1000))")
        }
        model.showInspection = false
        try await Task.sleep(nanoseconds: 150_000_000)
        guard model.backend?.process.isRunning == true else { fatalError("查看详情后解析核心退出") }
        await model.render(3)
        guard model.frame == 3 else { fatalError("查看详情后无法继续定位：\(model.status)") }
        model.inspectAnimation()
        for _ in 0..<1400 {
            if !model.working { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        model.showInspection = false
        await model.open(source.path); await model.selectItem(); await model.render(5)
        guard model.backend?.process.isRunning == true, model.frame == 5, model.image != nil else {
            fatalError("重复查看详情后无法切换并播放素材：\(model.status)")
        }
        print("Animation inspection: DLL metadata via Parallels, exact game path reference, nested structure; source unchanged")
        print("Inspection continuity: repeated details, frame seeking and resource switch preserve the backend")
    }
}
