using static IIDXResourceViewer.I18n;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace IIDXResourceViewer;

public sealed class Backend : IDisposable
{
    private readonly Process process;
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> pending = new();
    private readonly SemaphoreSlim writer = new(1, 1);
    private readonly string cache;
    private readonly Task logTask;
    private readonly Task readTask;
    private int serial;
    private int disposed;
    private readonly bool background;
    private static readonly ConcurrentDictionary<Task, byte> cleanupTasks = new();
    public static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };
    public string LogPath => Path.Combine(cache, "backend.log");

    public bool IsRunning => disposed == 0 && !process.HasExited;

    public Backend(string home, bool background = false)
    {
        this.background = background;
        cache = Path.Combine(Path.GetTempPath(), "iidx-winui-" + Guid.NewGuid());
        Directory.CreateDirectory(cache);
        var python = Path.Combine(home, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python)) throw new InvalidOperationException("请先运行 start_windows.bat，安装解析核心。");
        var start = new ProcessStartInfo(python) {
            WorkingDirectory = home, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8, StandardInputEncoding = new UTF8Encoding(false)
        };
        start.ArgumentList.Add(Path.Combine(home, "backend.py"));
        start.ArgumentList.Add("--cache"); start.ArgumentList.Add(cache);
        start.Environment["QT_QPA_PLATFORM"] = "offscreen";
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        start.Environment["PYTHONUNBUFFERED"] = "1";
        process = Process.Start(start) ?? throw new InvalidOperationException("解析核心启动失败");
        readTask = Task.Run(ReadResponses);
        logTask = Task.Run(async () => {
            await using var log = new StreamWriter(LogPath, false, Encoding.UTF8);
            while (await process.StandardError.ReadLineAsync() is string line) {
                await log.WriteLineAsync(line); await log.FlushAsync();
            }
        });
    }

    private async Task ReadResponses()
    {
        try {
            while (await process.StandardOutput.ReadLineAsync() is string line) {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                if (!pending.TryRemove(root.GetProperty("id").GetInt32(), out var completion)) continue;
                if (root.GetProperty("ok").GetBoolean()) completion.SetResult(root.GetProperty("result").Clone());
                else completion.SetException(new InvalidOperationException(root.GetProperty("error").GetString()));
            }
            throw new IOException("解析核心已退出，请检查临时目录中的 backend.log。");
        } catch (Exception ex) {
            foreach (var id in pending.Keys) if (pending.TryRemove(id, out var completion)) completion.TrySetException(ex);
        }
    }

    public async Task<T> Request<T>(string method, Dictionary<string, object>? args = null)
    {
        if (!IsRunning) throw new IOException("解析核心已退出");
        int id = Interlocked.Increment(ref serial);
        args = args is null ? new() : new(args);
        args["id"] = id; args["method"] = method;
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = completion;
        await writer.WaitAsync().ConfigureAwait(false);
        try {
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(args)).ConfigureAwait(false);
            await process.StandardInput.FlushAsync().ConfigureAwait(false);
        } catch {
            pending.TryRemove(id, out _); throw;
        } finally { writer.Release(); }
        var reply = await completion.Task.ConfigureAwait(false);
        return reply.Deserialize<T>(JsonOptions) ?? throw new IOException("解析核心返回了无效数据");
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        foreach (var id in pending.Keys)
            if (pending.TryRemove(id,out var completion)) completion.TrySetException(new OperationCanceledException("已取消"));
        if (background) {
            var cleanup = Task.Run(Cleanup); cleanupTasks[cleanup] = 0;
            _ = cleanup.ContinueWith(done => cleanupTasks.TryRemove(done,out _),TaskScheduler.Default);
        } else Cleanup();
    }

    public static void FinishCleanup()
    {
        try { Task.WaitAll(cleanupTasks.Keys.ToArray(),3000); } catch { }
    }

    private void Cleanup()
    {
        try {
            if (background && !process.HasExited) process.Kill(true);
            else {
                process.StandardInput.Close();
                if (!process.WaitForExit(1000)) process.Kill(true);
            }
            process.WaitForExit();
            Task.WaitAll(new[] { readTask, logTask }, 2000);
            process.Dispose();
            Directory.Delete(cache, true);
        } catch { /* The OS can keep a native media stream open during window teardown. */ }
    }
}

public sealed record FileEntry(string Path, string Relative, string Name, string Version, string Kind, long Size, string? Song_title = null)
{
    public string Subtitle => $"{Relative} · {Tr(Kind)} · {Size / 1024.0:0.0} KB";
}
public sealed record ScanReply(string Root, List<FileEntry> Files);
public sealed record AnimationItem(int Id, string Name, int Length, double Fps, int Width, int Height, List<string> Warnings);
public sealed record AssetItem(int Id, string Name, string Kind, long Size, string Details, bool Replaceable = false);
public sealed record PackageInfo(string Path, List<AssetItem> Assets, List<AnimationItem> Animations, List<string> Warnings,
    bool Modified = false, int Change_count = 0, bool Can_undo = false, string? Selected_asset = null, string? Edit_notice = null, EngineInfo? Engine = null, string? Format = null, bool Song_preview = false,
    string? Song_title = null, string? Song_artist = null)
{
    public string DisplayTitle => string.Join(" · ",new[] {System.IO.Path.GetFileName(Path),Song_title,Song_artist}.Where(s=>!string.IsNullOrWhiteSpace(s)));
}
public sealed record GameLibraryInfo(string Path, string Architecture, string? Version);
public sealed record EngineInfo(string Family, GameLibraryInfo? Library)
{
    public string Label => Family is "Live2D" or "27+" ? "27代+/SDVX6+" : Family == "AP2" ? "通用动画" : $"{Family.Replace("-", "–")} 代";
    public string Detail => Library is null ? "按格式和目录识别" : $"{System.IO.Path.GetFileName(Library.Path)} · {Library.Architecture}";
}
public sealed record PreviewReply(string Kind, string? Path, int? Frame, string? Text, List<string>? Warnings, List<AssetItem>? Assets, PackageInfo? Package,
    int? Width = null, int? Height = null, int? Stride = null, long? Offset = null, string? Pixel_format = null, RendererInfo? Renderer = null);
public sealed record RendererInfo(bool Hardware, string? Api, string? Device, string? Reason);
public sealed record ExportReply(string Path);
public sealed record AudioExportInfo(string Name);
public sealed record ContentItem(string Name, string Subtitle, string Type, int Index, string Group = "", bool IsHeader = false, bool Expanded = true)
{
    public string DisplayName=>IsHeader ? Tr(Group)+Name[Name.LastIndexOf(" · ",StringComparison.Ordinal)..] : Type=="song" ? Tr(Name) : Name;
    public string DisplaySubtitle=>Type=="asset"&&Subtitle.Contains(" · ") ? Tr(Subtitle[..Subtitle.IndexOf(" · ",StringComparison.Ordinal)])+Subtitle[Subtitle.IndexOf(" · ",StringComparison.Ordinal)..] : Tr(Subtitle);
    public string HeaderGlyph => Expanded ? "\uE70D" : "\uE76C";
    public Microsoft.UI.Xaml.Visibility HeaderVisibility => IsHeader ? Microsoft.UI.Xaml.Visibility.Visible : Microsoft.UI.Xaml.Visibility.Collapsed;
}
