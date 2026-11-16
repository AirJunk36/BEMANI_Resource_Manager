import AppKit
import Foundation

@main
struct MacInspectionSmoke {
    @MainActor
    static func main() async throws {
        let model=ViewerModel()
        defer { model.backend?.stop() }
        let root=model.home.deletingLastPathComponent()
        for relative in ["33_Sparkle_Shower/data/graphic/0/title.ifs", "33_Sparkle_Shower/data/graphic/1/title.ifs"] {
            let source=root.appendingPathComponent(relative)
            let before=try Data(contentsOf: source)
            await model.open(source.path); await model.selectItem()
            guard model.image != nil else { fatalError("预览失败：\(model.status)") }
            for _ in 0..<2 {
                model.inspectAnimation()
                for _ in 0..<1400 {
                    if !model.working { break }
                    try await Task.sleep(nanoseconds: 50_000_000)
                }
                guard model.showInspection, model.inspectionText.contains("DLL 读取根时间轴") else {
                    fatalError("详情读取失败：\(model.status) / \(model.inspectionText.prefix(1600))")
                }
                model.showInspection=false
                try await Task.sleep(nanoseconds: 150_000_000)
                guard model.backend?.process.isRunning == true else { fatalError("关闭详情后核心退出") }
                await model.render(3)
                guard model.frame == 3 else { fatalError("详情后无法定位：\(model.status)") }
                model.togglePlay()
                try await Task.sleep(nanoseconds: 180_000_000)
                model.tick()
                model.pause()
                for _ in 0..<500 {
                    if !model.renderBusy { break }
                    try await Task.sleep(nanoseconds: 10_000_000)
                }
                guard model.frame > 3 else { fatalError("详情后无法继续播放") }
            }
            guard try Data(contentsOf: source)==before else { fatalError("源文件改变") }
            print("PASS: repeated DLL inspection, seeking and playback; source unchanged: \(relative)")
        }
        let next=root.appendingPathComponent("20_tricoro/data/graphic/test_effect_anime.ifs")
        await model.open(next.path); await model.selectItem(); await model.render(5)
        guard model.backend?.process.isRunning == true, model.frame==5, model.image != nil else {
            fatalError("详情后无法切换资源：\(model.status)")
        }
        print("PASS: resource switch after inspection preserves the same backend")
    }
}
