using static IIDXResourceViewer.I18n;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace IIDXResourceViewer;

public sealed record ExportContext(string Path, string Context_path, List<string> Protected_roots, List<string> Hidden_layers,JsonElement? Live2d_pose=null);
public sealed record SongVisuals(string Title, string? Bga_path, string? Layer_path, double Bga_offset, string Layer_animation);
public sealed record LayerChoice(int Id, string Name) { public string DisplayName=>Id==-2?Tr(Name):Name; }
public sealed record LayerState(string Id,string Name) { public string DisplayName=>Tr(Name); }
public sealed record LayerChoices(List<LayerChoice> Animations, int Selected,List<LayerState>? States=null,bool Gauge=false);
public sealed record RelatedSong(string Id, string Name, string Path, string? Working_path);
public sealed record RelatedSongs(List<RelatedSong> Songs);

// A separate core keeps long media jobs away from the interactive preview queue.
public sealed class BackgroundCore : IDisposable
{
    public readonly Backend Backend;
    private ExportContext context;
    public BackgroundCore(string home, ExportContext context) { this.context = context; Backend = new(home, background:true); }
    public async Task Open() {
        _ = await Backend.Request<JsonElement>("open", new() { ["path"] = context.Path, ["protected_roots"] = context.Protected_roots });
        if(context.Live2d_pose is {ValueKind:JsonValueKind.Array} pose&&pose.GetArrayLength()>0)
            _ = await Backend.Request<JsonElement>("live2d_edit",new(){["operation"]="pose",["keys"]=pose});
    }
    public async Task OpenSong(string path, string? workingPath = null) {
        _ = await Backend.Request<JsonElement>("open", new() { ["path"] = workingPath ?? path, ["protected_roots"] = context.Protected_roots });
        context = context with { Path = workingPath ?? path, Context_path = path };
    }
    public Task<T> Request<T>(string method, Dictionary<string,object>? args = null) {
        args = args is null ? new() : new(args); args["context_path"] = context.Context_path;
        return Backend.Request<T>(method,args);
    }
    public void Dispose() => Backend.Dispose();
}

public sealed partial class MainWindow
{
    private BackgroundCore? mediaJob;
    private async void ExportMP4_Click(object sender, RoutedEventArgs e)
    {
        if (working || package is null || animation is null && asset?.Kind != "视频") return;
        Pause();
        var picker = new FolderPicker(); picker.FileTypeFilter.Add("*"); InitializePicker(picker);
        var folder = await picker.PickSingleFolderAsync(); if (folder is null) return;
        string name = string.Join("_",PreviewTitle.Text.Split(System.IO.Path.GetInvalidFileNameChars())) + ".mp4";
        var args = new Dictionary<string,object> { ["resolution"] = PreviewResolution, ["fps"] = (int)AnimationPlaybackFps, ["renderer"] = RenderMode };
        if (animation is not null) args["animation"] = animation.Id; else args["asset"] = asset!.Id;
        ExportContext context;
        try { context = await Request<ExportContext>("export_context",args); }
        catch (Exception ex) { ShowError(ex); return; }
        args["hidden_layers"] = context.Hidden_layers; args["kind"] = "mp4"; args["target"] = System.IO.Path.Combine(folder.Path,name);
        var status = new TextBlock { Text = Tr("正在导出 MP4…"), TextWrapping = TextWrapping.Wrap };
        var ring = new ProgressRing { IsActive = true, Width = 24, Height = 24 };
        var content = new StackPanel { Spacing = 16 }; content.Children.Add(ring); content.Children.Add(status);
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = Tr("导出 MP4"), Content = Tr(content), CloseButtonText = Tr("取消" )};
        bool cancelled = false;
        var worker = new BackgroundCore(home,context); mediaJob = worker;
        dialog.Closed += (_, _) => { cancelled = true; worker.Dispose(); if (mediaJob == worker) mediaJob = null; };
        var shown = dialog.ShowAsync();
        try {
            await worker.Open(); _ = await worker.Request<ExportReply>("export",args);
            if (!cancelled) { status.Text = Tr(Status.Text = Tr("已导出：" + args["target"])); dialog.CloseButtonText = Tr("关闭"); }
        } catch (Exception ex) { if (!cancelled) { status.Text = Tr(ex.Message); dialog.CloseButtonText = Tr("关闭"); } }
        finally { ring.IsActive = false; worker.Dispose(); if (mediaJob == worker) mediaJob = null; }
        await shown;
    }
}
