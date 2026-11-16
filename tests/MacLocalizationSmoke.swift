import AppKit
import SwiftUI

@main
struct MacLocalizationSmoke {
    @MainActor static func main() async throws {
        NSApplication.shared.setActivationPolicy(.accessory)
        let language=AppLanguage.shared
        let old=language.code
        defer { language.code=old }
        language.code="en"
        guard tr("资源库")=="Library",tr("副本 · 3 次修改")=="Copy · 3 changes",
              tr("播放失败：请选择有效谱面")=="Playback failed: Choose a valid chart",
              tr("原始：30 帧；预览：60 帧")=="Source: 30 frames; preview: 60 frames",
              tr("ICARUS")=="ICARUS" else { fatalError("English interface lookup/interpolation") }
        guard tr("2DX 第 17 个音频头无效")=="Invalid audio header for 2DX track 17",
              tr("播放失败：2DX 加密头被截断")=="Playback failed: Truncated encrypted 2DX header",
              tr("媒体转换失败：ffmpeg detail\n2DX 音频范围无效")=="Media conversion failed: ffmpeg detail\nInvalid 2DX audio range",
              tr("全部")=="All",tr("导出 GIF…")=="Export GIF…" else { fatalError("English parser diagnostics or GIF controls") }
        let model=ViewerModel();defer { model.backend?.stop() }
        guard exportName("日本語",fallback:"song_1",replaced:true)=="song_1_replaced",exportName("title_replaced",replaced:true)=="title_replaced" else { fatalError("English replacement/export names") }
        let game=URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        await model.scan(game.appendingPathComponent("33_Sparkle_Shower").path);await model.library.waitForResults()
        guard model.files.count>10000 else { fatalError("Folder loading failed: \(model.status)") }
        let invalid=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString+".2dx")
        try Data(count:96).write(to:invalid);defer { try? FileManager.default.removeItem(at:invalid) }
        await model.open(invalid.path)
        guard tr(model.status)=="Unsupported 2DX format or encryption" else { fatalError("Backend error still untranslated: \(model.status)") }
        let file=game.appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs")
        await model.open(file.path);await model.selectItem()
        let originalName=model.title
        guard model.image != nil else { fatalError("Native preview failed") }
        let out=URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_LOCALIZATION_OUTPUT"]!)
        for code in ["en","zh-Hans"] {
            language.code=code
            var frames:[String:CGRect]=[:]
            let view=ViewerWindow(model:model,startAutomatically:false,layoutObserver:{frames=$0})
            let host=NSHostingView(rootView:view.background(Color(nsColor:.windowBackgroundColor)))
            let window=NSWindow(contentRect:NSRect(x:0,y:0,width:1460,height:920),styleMask:[.titled,.resizable],backing:.buffered,defer:false)
            window.isReleasedWhenClosed=false;window.contentView=host;window.orderBack(nil)
            for _ in 0..<15 { host.layoutSubtreeIfNeeded();window.displayIfNeeded();try await Task.sleep(for:.milliseconds(25)) }
            guard model.title==originalName,let playback=frames["playback"],let status=frames["status"],playback.maxY<=status.minY+1 else { fatalError("Language switch changed selection or obscured playback") }
            if let image=host.bitmapImageRepForCachingDisplay(in:host.bounds) { host.cacheDisplay(in:host.bounds,to:image);try image.representation(using:.png,properties:[:])!.write(to:out.appendingPathComponent("interface-"+code+".png")) }
            window.close()
        }
        await model.open(game.appendingPathComponent("20_tricoro/data/sound/20032/20032.2dx").path)
        for _ in 0..<100 { if !model.working { break };try await Task.sleep(for:.milliseconds(25)) }
        model.selectedItem="song";await model.selectItem();language.code="en"
        guard model.displayTitle=="Full song preview",model.title=="完整乐曲预览" else { fatalError("Full song preview heading localization") }
        let songHost=NSHostingView(rootView:ViewerWindow(model:model,startAutomatically:false).background(Color(nsColor:.windowBackgroundColor)))
        let songWindow=NSWindow(contentRect:NSRect(x:0,y:0,width:1460,height:920),styleMask:[.titled,.resizable],backing:.buffered,defer:false)
        songWindow.isReleasedWhenClosed=false;songWindow.contentView=songHost;songWindow.orderBack(nil)
        for _ in 0..<60 { songHost.layoutSubtreeIfNeeded();songWindow.displayIfNeeded();try await Task.sleep(for:.milliseconds(50)) }
        if let image=songHost.bitmapImageRepForCachingDisplay(in:songHost.bounds) { songHost.cacheDisplay(in:songHost.bounds,to:image);try image.representation(using:.png,properties:[:])!.write(to:out.appendingPathComponent("interface-song-en.png")) }
        language.code="zh-Hans"
        guard model.displayTitle=="完整乐曲预览",model.selectedItem=="song" else { fatalError("Song title switch changed selection") }
        songWindow.close()
        guard tr("资源库")=="资源库" else { fatalError("Chinese restore") }
        print("PASS Chinese/English interface, interpolation, native folder load, preview and playback layout")
    }
}
