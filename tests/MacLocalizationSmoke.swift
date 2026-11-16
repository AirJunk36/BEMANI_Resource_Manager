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
        let model=ViewerModel();defer { model.backend?.stop() }
        let game=URL(fileURLWithPath:ProcessInfo.processInfo.environment["IIDX_GAME_ROOT"]!)
        await model.scan(game.appendingPathComponent("33_Sparkle_Shower").path);await model.library.waitForResults()
        guard model.files.count>10000 else { fatalError("Folder loading failed: \(model.status)") }
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
