import Foundation
import Combine

final class AppLanguage: ObservableObject {
    static let shared = AppLanguage()
    @Published var code: String {
        didSet { UserDefaults.standard.set(code, forKey:"interfaceLanguage") }
    }
    var english: Bool { code.hasPrefix("en") }
    init() {
        code = ProcessInfo.processInfo.environment["BEMANI_LANGUAGE"] ?? UserDefaults.standard.string(forKey:"interfaceLanguage")
            ?? (Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh-Hans" : "en")
    }
}

private enum Translations {
    static let values: [String:String] = {
        let cwd = URL(fileURLWithPath:FileManager.default.currentDirectoryPath)
        let locations = [Bundle.main.resourceURL?.appendingPathComponent("Core/iidxviewer/locales/en.json"),cwd.appendingPathComponent("iidxviewer/locales/en.json")].compactMap { $0 }
        for url in locations {
            if let data=try? Data(contentsOf:url),let result=try? JSONDecoder().decode([String:String].self,from:data) { return result }
        }
        return [:]
    }()
    static let slots = try! NSRegularExpression(pattern:#"\{(\d+)\}"#)
    static let patterns: [(NSRegularExpression,String,[String])] = values.compactMap { key,value -> (NSRegularExpression,String,[String])? in
        let source=key as NSString;let matches=slots.matches(in:key,range:NSRange(location:0,length:source.length))
        guard !matches.isEmpty else { return nil }
        var expression="^",previous=0;var names:[String]=[]
        for match in matches {
            expression += NSRegularExpression.escapedPattern(for:source.substring(with:NSRange(location:previous,length:match.range.location-previous)))+"(.*?)"
            names.append(source.substring(with:match.range));previous=match.range.location+match.range.length
        }
        expression += NSRegularExpression.escapedPattern(for:source.substring(from:previous))+"$"
        guard let regex=try? NSRegularExpression(pattern:expression,options:.dotMatchesLineSeparators) else { return nil }
        return (regex,value,names)
    }.sorted { $0.0.pattern.count > $1.0.pattern.count }
    static let lock=NSLock()
    static var cache: [String:String]=[:]
}

func englishExportTitle(_ text: String) -> String { Translations.values[text] ?? text }

func tr(_ text: String) -> String {
    guard AppLanguage.shared.english,text.unicodeScalars.contains(where:{ (0x4e00...0x9fff).contains($0.value) }) else { return text }
    if let value=Translations.values[text] { return value }
    Translations.lock.lock();let cached=Translations.cache[text];Translations.lock.unlock()
    if let cached { return cached }
    var value=text;let source=text as NSString
    for (regex,template,names) in Translations.patterns {
        guard let match=regex.firstMatch(in:text,range:NSRange(location:0,length:source.length)) else { continue }
        value=template
        for (i,name) in names.enumerated() { value=value.replacingOccurrences(of:name,with:tr(source.substring(with:match.range(at:i+1)))) }
        break
    }
    if value==text {
        if text.contains("\n") { value=text.components(separatedBy:"\n").map(tr).joined(separator:"\n") }
        else if let colon=text.firstIndex(of:"："),let label=Translations.values[String(text[..<colon]).trimmingCharacters(in:.whitespaces)] {
            let indent=String(text.prefix(while:{$0==" "}));value=indent+label+": "+tr(String(text[text.index(after:colon)...]))
        }
    }
    Translations.lock.lock();if Translations.cache.count>=4096 { Translations.cache.removeAll(keepingCapacity:true) };Translations.cache[text]=value;Translations.lock.unlock()
    return value
}
