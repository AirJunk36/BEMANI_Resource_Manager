using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;

namespace IIDXResourceViewer;

public static class I18n
{
    private static readonly string Preference=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"BEMANIResourceManager","language.txt");
    public static string Code { get; private set; }=InitialLanguage();
    public static bool English=>Code.StartsWith("en",StringComparison.OrdinalIgnoreCase);
    public static event Action? Changed;
    private static string InitialLanguage() {
        string? value=Environment.GetEnvironmentVariable("BEMANI_LANGUAGE");
        if(value is null)try{value=File.ReadAllText(Preference).Trim();}catch(IOException){}catch(UnauthorizedAccessException){}
        return value is "en" or "zh-Hans" ? value : CultureInfo.CurrentUICulture.TwoLetterISOLanguageName=="zh" ? "zh-Hans" : "en";
    }
    public static void SetLanguage(string code,bool persist=true) {
        if(code is not ("en" or "zh-Hans"))return;
        Code=code;
        if(persist) { Directory.CreateDirectory(Path.GetDirectoryName(Preference)!);File.WriteAllText(Preference,code); }
        Changed?.Invoke();
    }
    private static readonly Dictionary<string,string> Values=Load();
    private static Dictionary<string,string> Load() {
        foreach(string root in new[]{AppContext.BaseDirectory,Environment.CurrentDirectory}) {
            string file=Path.Combine(root,"iidxviewer","locales","en.json");
            if(File.Exists(file))return JsonSerializer.Deserialize<Dictionary<string,string>>(File.ReadAllText(file))??new();
        }
        return new();
    }
    private sealed record Pattern(Regex Regex,string Value,string[] Slots);
    private static readonly List<Pattern> Patterns=Values.Where(p=>Regex.IsMatch(p.Key,@"\{\d+\}")).Select(p=>{
        var slots=Regex.Matches(p.Key,@"\{\d+\}").Select(m=>m.Value).ToArray();
        string regex="\\A"+Regex.Replace(Regex.Escape(p.Key),@"\\\{\d+}","(.*?)")+"\\z";
        return new Pattern(new Regex(regex,RegexOptions.Singleline|RegexOptions.CultureInvariant,TimeSpan.FromMilliseconds(50)),p.Value,slots);
    }).OrderByDescending(p=>p.Regex.ToString().Length).ToList();
    private static readonly Dictionary<string,string> Cache=new();
    private sealed record OriginalText(string Value);
    private static readonly ConditionalWeakTable<string,OriginalText> Originals=new();
    private static string Remember(string shown,string original) {
        if(!ReferenceEquals(shown,original))Originals.GetValue(shown,_=>new OriginalText(original));
        return shown;
    }
    public static T Tr<T>(T value)=>value is string text ? (T)(object)Translate(text) : value;
    private static string Translate(string text) {
        if(!English||!text.Any(c=>c is >= '\u4e00' and <= '\u9fff'))return text;
        if(Values.TryGetValue(text,out string? direct))return Remember(direct,text);
        lock(Cache)if(Cache.TryGetValue(text,out string? cached))return Remember(cached,text);
        string result=text;
        foreach(var pattern in Patterns) {
            var match=pattern.Regex.Match(text);if(!match.Success)continue;
            result=pattern.Value;
            for(int i=0;i<pattern.Slots.Length;i++)result=result.Replace(pattern.Slots[i],Translate(match.Groups[i+1].Value),StringComparison.Ordinal);
            break;
        }
        if(result==text) {
            if(text.Contains('\n'))result=string.Join("\n",text.Split('\n').Select(Translate));
            else {
                int colon=text.IndexOf('：');
                if(colon>=0&&Values.TryGetValue(text[..colon].Trim(),out string? label))result=new string(' ',text.TakeWhile(c=>c==' ').Count())+label+": "+text[(colon+1)..];
            }
        }
        lock(Cache){if(Cache.Count>=4096)Cache.Clear();Cache[text]=result;}return Remember(result,text);
    }
    private sealed class State {
        public readonly Dictionary<string,(string Original,string Shown)> Strings=new();
        public bool Watched;
    }
    private static readonly ConditionalWeakTable<DependencyObject,State> States=new();
    private static string Display(DependencyObject item,string name,string text) {
        var state=States.GetOrCreateValue(item);
        string original=state.Strings.TryGetValue(name,out var old)&&old.Shown==text ? old.Original : Originals.TryGetValue(text,out var origin) ? origin.Value : text;
        string shown=Tr(original);state.Strings[name]=(original,shown);return shown;
    }
    public static void Apply(DependencyObject root) {
        if(root is FrameworkElement element) {
            var state=States.GetOrCreateValue(root);
            if(!state.Watched){state.Watched=true;element.Loaded+=(_,_)=>Apply(element);}
        }
        if(root is TextBlock label&&label.Tag as string!="data")label.Text=Display(root,"Text",label.Text);
        if(root is TextBox input)input.PlaceholderText=Display(root,"PlaceholderText",input.PlaceholderText);
        if(root is ContentControl content&&content.Content is string text&&root is not ComboBoxItem)content.Content=Display(root,"Content",text);
        if(ToolTipService.GetToolTip(root) is string tip)ToolTipService.SetToolTip(root,Display(root,"Tooltip",tip));
        if(root is ComboBox choice&&choice.ItemTemplate is null&&(choice.ItemsSource is IEnumerable<string>||choice.Items.Cast<object>().Any(x=>x is string))) {
            choice.ItemTemplate=ChoiceTemplate();
        }
        if(root is ComboBox cb&&cb.Header is string ch)cb.Header=Display(root,"Header",ch);
        if(root is NumberBox nb&&nb.Header is string nh)nb.Header=Display(root,"Header",nh);
        if(root is TextBox tb&&tb.Header is string th)tb.Header=Display(root,"Header",th);
        if(root is Slider sl&&sl.Header is string sh)sl.Header=Display(root,"Header",sh);
        if(root is Expander ex&&ex.Header is string eh)ex.Header=Display(root,"Header",eh);
        if(root is TabViewItem tab&&tab.Header is string title)tab.Header=Display(root,"Header",title);
        if(root is Button button&&button.Flyout is { } flyout)ApplyFlyout(flyout);
        if(root is DropDownButton drop&&drop.Flyout is { } menu)ApplyFlyout(menu);
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)Apply(VisualTreeHelper.GetChild(root,i));
    }
    public static DataTemplate ChoiceTemplate()=>(DataTemplate)XamlReader.Load("<DataTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'><TextBlock Text='{Binding Converter={StaticResource InterfaceText}}'/></DataTemplate>");
    private static void ApplyFlyout(FlyoutBase flyout) {
        var state=States.GetOrCreateValue(flyout);
        if(!state.Watched){state.Watched=true;flyout.Opened+=(_,_)=>ApplyFlyout(flyout);}
        if(flyout is Flyout panel&&panel.Content is { } content)Apply(content);
        if(flyout is MenuFlyout menu)foreach(var item in menu.Items) {
            if(item is MenuFlyoutItem action)action.Text=Display(action,"Text",action.Text);
            if(item is MenuFlyoutSubItem group) {
                group.Text=Display(group,"Text",group.Text);
                foreach(var child in group.Items)if(child is MenuFlyoutItem leaf)leaf.Text=Display(leaf,"Text",leaf.Text);
            }
        }
    }
    public static void RefreshChoices(DependencyObject root) {
        if(root is ComboBox choice&&(choice.ItemsSource is IEnumerable<string>||choice.Items.Cast<object>().Any(x=>x is string)))choice.ItemTemplate=ChoiceTemplate();
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++)RefreshChoices(VisualTreeHelper.GetChild(root,i));
    }
}

public sealed class InterfaceText : DependencyObject, IValueConverter {
    public object Convert(object value,Type targetType,object parameter,string language)=>I18n.Tr(value);
    public object ConvertBack(object value,Type targetType,object parameter,string language)=>throw new NotSupportedException();
}
