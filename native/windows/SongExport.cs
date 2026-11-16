using static IIDXResourceViewer.I18n;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Media.Core;
using Windows.Media.Playback;
using Windows.Storage.Pickers;

namespace IIDXResourceViewer;

public sealed record SongBank(string Id, string Name) { public string DisplayName=>Tr(Name); }
public sealed record SongChart(int Id, string Name, string? Bank = null) { public string DisplayName=>Tr(Name); }
public sealed record SongInfo(List<SongBank> Banks, string Bank, List<SongChart> Charts, bool Needs_chart);

// Owns the native player and one cancellable media worker for this dialog.
internal sealed class SongStudioPane : StackPanel, IDisposable
{
    private readonly string home, renderMode;
    private readonly bool previewOnly;
    private TimeSpan? pendingPosition;
    private readonly Action<object> initializePicker;
    private readonly Action<BackgroundCore?> workerChanged;
    private ExportContext context;
    private BackgroundCore? core;
    private readonly Backend? audioBackend;
    private readonly AssetItem? originalAsset;
    private AudioExportInfo? originalInfo;
    private readonly ComboBox audioSource = new() { Header = Tr("来源"), ItemsSource = new[] { "完整乐曲", "原音轨" }, SelectedIndex = 0, Visibility = Visibility.Collapsed };
    private readonly TextBlock originalLabel = new() { TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
    private readonly StackPanel songSettings = new() { Spacing = 10 };
    public bool OriginalAudio => audioSource.SelectedIndex == 1;
    private CancellationTokenSource? cancellation;
    private bool updating, disposed;
    private bool hasLayerGauge;
    private int revision;
    private SongInfo? info;
    private string? chartPath;
    private string bgaPath = "", layerPath = "", songPath, loadedLayerPath = "", preferredLayer = "";
    private readonly TextBlock status = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
    private readonly ProgressRing ring = new() { Width = 22, Height = 22, IsActive = false };
    private readonly MediaPlayer player = new() { AutoPlay = false };
    private readonly MediaPlayerElement video = new() { Height = 180, AreTransportControlsEnabled = true, Stretch = Stretch.Uniform };
    private readonly StackPanel settings = new() { Spacing = 10 };
    private readonly StackPanel videoSettings = new() { Spacing = 10, Visibility = Visibility.Collapsed };
    private readonly ComboBox mode = new() { ItemsSource = new[] { "音频", "视频" }, SelectedIndex = 0, Width = 150, HorizontalAlignment = HorizontalAlignment.Right };
    private readonly StackPanel commands = new() { Orientation = Orientation.Horizontal, Spacing = 8 };
    private readonly ComboBox songs = new() { Header = Tr("对应乐曲"), DisplayMemberPath = "Name", HorizontalAlignment = HorizontalAlignment.Stretch, Visibility = Visibility.Collapsed };
    private readonly ComboBox banks = new() { Header = Tr("音频包"), DisplayMemberPath = "DisplayName", MinWidth = 240, HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly ComboBox charts = new() { Header = Tr("谱面"), DisplayMemberPath = "DisplayName", MinWidth = 150 };
    private readonly ComboBox layers = new() { Header = Tr("Layer 动画"), DisplayMemberPath = "DisplayName", Visibility = Visibility.Collapsed, MinWidth = 160 };
    private readonly ComboBox layerState = new() { Header=Tr("状态"),DisplayMemberPath="DisplayName",Visibility=Visibility.Collapsed,MinWidth=100 };
    private readonly Slider layerGauge = new() { Header=Tr("血量（%）"),Minimum=0,Maximum=100,Value=100,StepFrequency=1,Width=160,Visibility=Visibility.Collapsed };
    private readonly ComboBox resolution = new() { Header = Tr("分辨率"), ItemsSource = new[] { "480p", "720p", "1080p", "720p（竖屏）", "1080p（竖屏）" }, SelectedIndex = 1 };
    private readonly ComboBox fps = new() { Header = Tr("帧率"), ItemsSource = new[] { "60 FPS", "120 FPS" }, SelectedIndex = 0 };
    private readonly CheckBox bga = new() { Content = Tr("BGA" )}, layer = new() { Content = Tr("Layer" )};
    private readonly CheckBox bgaLoop = new() { Content = Tr("循环"), IsChecked = true }, layerLoop = new() { Content = Tr("循环" )}, blackKey = new() { Content = Tr("黑色透明" )};
    private readonly NumberBox bgaOffset = OffsetBox(), layerOffset = OffsetBox();
    private readonly TextBox bgaName = PathInput("选择或输入 BGA 路径"), layerName = PathInput("选择或输入 Layer 路径");
    private readonly Button previewButton = new() { Content = Tr("试听音频" )}, exportButton = new() { Content = Tr("导出 WAV…" )};
    private readonly Button cancelButton = new() { Content = Tr("取消处理"), Visibility = Visibility.Collapsed };
    public bool IsBusy { get; private set; }
    public string Title { get; private set; } = "乐曲";
    public string StatusText => status.Text;
    public MediaPlayer Player => player;
    internal ComboBox OutputResolution => resolution;
    internal ComboBox OutputFPS => fps;
    internal FrameworkElement SettingsPanel => settings;
    internal TextBox BGAPathInput => bgaName;
    internal TextBox LayerPathInput => layerName;
    internal ComboBox LayerAnimations => layers;
    internal ComboBox LayerStates => layerState;
    internal Slider LayerGauge => layerGauge;
    internal CheckBox BGAPlayback => bga;
    internal CheckBox LayerPlayback => layer;
    internal bool HasPreviewUI => Children.Contains(video);
    public bool VideoMode { get => mode.SelectedIndex == 1; set => mode.SelectedIndex = value ? 1 : 0; }
    public event Action? StateChanged;
    internal void RefreshLanguage() {
        bool previous=updating;updating=true;
        try {
            foreach(var choice in new[]{banks,charts,layers,layerState}) {
                var items=choice.ItemsSource;var selected=choice.SelectedItem;
                choice.ItemsSource=null;choice.ItemsSource=items;choice.SelectedItem=selected;
            }
            I18n.RefreshChoices(this);I18n.Apply(this);
        } finally { updating=previous; }
    }
    private static NumberBox OffsetBox() => new() { Header = Tr("偏移（秒）"), Value = 0, Minimum = -1800, Maximum = 1800, Width = 125, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
    private static TextBox PathInput(string placeholder) => new() { PlaceholderText = Tr(placeholder), HorizontalAlignment = HorizontalAlignment.Stretch };
    private static string InputPath(string path) => Environment.ExpandEnvironmentVariables(path.Trim().Trim('"'));
    private static StackPanel Row(params UIElement[] children) {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        foreach (var child in children) row.Children.Add(child);
        return row;
    }
    private static Grid FileRow(CheckBox check, TextBox name, Button choose, Button clear) {
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); grid.ColumnDefinitions.Add(new() { Width = new(1,GridUnitType.Star) }); grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        Grid.SetColumn(name,1); Grid.SetColumn(choose,2); Grid.SetColumn(clear,3); grid.Children.Add(check); grid.Children.Add(name); grid.Children.Add(choose); grid.Children.Add(clear); return grid;
    }
    public SongStudioPane(string home, ExportContext context, string renderMode, Action<object> initializePicker, Action<BackgroundCore?> workerChanged, Backend? audioBackend = null, AssetItem? originalAsset = null, bool previewOnly = false)
    {
        this.previewOnly = previewOnly;
        this.home = home; this.context = context; this.renderMode = renderMode; this.initializePicker = initializePicker; this.workerChanged = workerChanged;
        Loaded+=(_,_)=>I18n.Apply(this);
        this.audioBackend = audioBackend; this.originalAsset = originalAsset?.Kind == "音频" ? originalAsset : null;
        songPath = context.Context_path; Spacing = 12; MinWidth = previewOnly ? 0 : 620;
        video.SetMediaPlayer(player);
        if (previewOnly) { video.Height = 260; Children.Add(video); player.IsLoopingEnabled = true; }
        else Children.Add(mode);
        player.MediaOpened += (_, _) => DispatcherQueue.TryEnqueue(() => {
            if (!disposed && pendingPosition is TimeSpan position) { player.PlaybackSession.Position = position; pendingPosition = null; }
        });
        player.MediaFailed += (_, e) => DispatcherQueue.TryEnqueue(() => { if (!disposed) status.Text = Tr(e.ErrorMessage); });
        var chooseSong = new Button { Content = Tr("选择乐曲…" )};
        var chooseChart = new Button { Content = Tr("选择谱面…"), VerticalAlignment = VerticalAlignment.Bottom };
        var chooseBGA = new Button { Content = Tr("选择 BGA…" )}; var chooseLayer = new Button { Content = Tr("选择 Layer…" )};
        var clearBGA = new Button { Content = Tr("清除" )}; var clearLayer = new Button { Content = Tr("清除" )};
        if (previewOnly) {
            var loop = new CheckBox { Content = Tr("循环"), IsChecked = true };
            loop.Checked += (_, _) => player.IsLoopingEnabled = true; loop.Unchecked += (_, _) => player.IsLoopingEnabled = false;
            settings.Children.Add(Row(charts,chooseChart)); settings.Children.Add(Row(bga,layer,loop)); settings.Children.Add(Row(chooseBGA,chooseLayer)); settings.Children.Add(layers);
        } else {
            settings.Children.Add(audioSource); settings.Children.Add(originalLabel); settings.Children.Add(songSettings);
            songSettings.Children.Add(songs); songSettings.Children.Add(chooseSong);
            songSettings.Children.Add(Row(banks,charts,chooseChart));
            videoSettings.Children.Add(FileRow(bga,bgaName,chooseBGA,clearBGA)); videoSettings.Children.Add(Row(bgaLoop,bgaOffset));
            videoSettings.Children.Add(FileRow(layer,layerName,chooseLayer,clearLayer)); videoSettings.Children.Add(Row(layerLoop,layerOffset,layers,blackKey));
            videoSettings.Children.Add(Row(resolution,fps)); settings.Children.Add(videoSettings);
        }
        settings.Children.Add(Row(layerState,layerGauge));
        Children.Add(new ScrollViewer { Content = Tr(settings), MaxHeight = previewOnly ? 230 : 315, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        Children.Add(status);
        var pause = new Button { Content = Tr("暂停" )};
        if (previewOnly) { commands.Children.Add(previewButton); commands.Children.Add(pause); } else commands.Children.Add(exportButton);
        commands.Children.Add(ring); Children.Add(commands);
        if (previewOnly) { commands.Children.Add(cancelButton); cancelButton.Click += (_, _) => Cancel(); }
        previewButton.Click += async (_, _) => { if (player.Source is not null) player.Play(); else await Preview(VideoMode); }; pause.Click += (_, _) => player.Pause();
        exportButton.Click += async (_, _) => await ChooseExport(VideoMode);
        mode.SelectionChanged += (_, _) => {
            if (!previewOnly) { player.Pause(); player.Source = null; }
            UpdateAudioMode();
        };
        audioSource.SelectionChanged += async (_, _) => {
            if (updating) return;
            player.Pause(); player.Source = null; mode.SelectedIndex = 0; UpdateAudioMode();
            if (!OriginalAudio && info is null) await Run("正在读取乐曲…",async token => await LoadInfo(await Connect(token),true));
        };
        chooseSong.Click += async (_, _) => {
            string? path = await Pick(".ifs",".2dx",".s3p"); if (path is null) return;
            await Run("正在读取乐曲…",async token => { var worker = await Connect(token); await OpenSong(worker,path); await LoadInfo(worker,true); });
        };
        chooseChart.Click += async (_, _) => {
            var path = await Pick(".1"); if (path is null) return; chartPath = path;
            await Run("正在读取谱面…",async token => await LoadInfo(await Connect(token),false));
            await RefreshPreview();
        };
        bgaName.TextChanged += (_, _) => { if (!updating) UpdateBGAPath(); };
        layerName.TextChanged += (_, _) => { if (!updating) UpdateLayerPath(); };
        clearBGA.Click += (_, _) => bgaName.Text = ""; clearLayer.Click += (_, _) => layerName.Text = "";
        chooseBGA.Click += async (_, _) => { var path = await Pick("*"); if (path is not null) { bgaName.Text = path; await RefreshPreview(); } };
        chooseLayer.Click += async (_, _) => {
            var path = await Pick("*"); if (path is null) return; layerName.Text = path;
            await Run("正在读取 Layer…",async token => await LoadLayer(await Connect(token)));
            await RefreshPreview();
        };
        songs.SelectionChanged += async (_, _) => {
            if (updating || songs.SelectedItem is not RelatedSong song) return;
            await Run("正在读取乐曲…",async token => { var worker = await Connect(token); await OpenSong(worker,song.Path,song.Working_path); await LoadInfo(worker,true); });
        };
        banks.SelectionChanged += async (_, _) => { if (!updating && banks.SelectedItem is SongBank) await Run("正在读取谱面…",async token => await LoadInfo(await Connect(token),false)); };
        charts.SelectionChanged += async (_, _) => {
            if (updating || charts.SelectedItem is not SongChart chart || info is null) return;
            if (chart.Bank is not null) { updating = true; banks.SelectedItem = info.Banks.FirstOrDefault(b => b.Id == chart.Bank) ?? banks.SelectedItem; updating = false; }
            await RefreshPreview();
        };
        layers.SelectionChanged += async (_, _) => { layerLoop.IsEnabled = (layers.SelectedItem as LayerChoice)?.Id != -2;UpdateStateControls(); await RefreshPreview(); };
        bga.Checked += async (_, _) => await RefreshPreview(); bga.Unchecked += async (_, _) => await RefreshPreview();
        layer.Checked += async (_, _) => await RefreshPreview(); layer.Unchecked += async (_, _) => await RefreshPreview();
        layerState.SelectionChanged += async (_,_)=> { if(!updating) await RefreshPreview(true); };
        layerGauge.PointerCaptureLost += async (_,_)=> { if(!updating) await RefreshPreview(); };
        layerGauge.KeyUp += async (_,_)=> { if(!updating) await RefreshPreview(); };
    }
    private void UpdateAudioMode() {
        songSettings.Visibility = OriginalAudio ? Visibility.Collapsed : Visibility.Visible;
        originalLabel.Visibility = OriginalAudio ? Visibility.Visible : Visibility.Collapsed;
        mode.Visibility = OriginalAudio ? Visibility.Collapsed : Visibility.Visible;
        videoSettings.Visibility = !OriginalAudio && VideoMode ? Visibility.Visible : Visibility.Collapsed;
        video.Height = previewOnly ? video.Height : !OriginalAudio && VideoMode ? 180 : 90;
        previewButton.Content = Tr("播放");
        exportButton.Content = Tr(OriginalAudio ? "导出原格式…" : VideoMode ? "导出 MP4…" : "导出 WAV…");
    }
    internal void PreviewHeight(double height) { if (previewOnly) video.Height = Math.Max(150,height-300); }
    internal void TogglePlayback() { if (player.PlaybackSession.PlaybackState == MediaPlaybackState.Playing) player.Pause(); else if (player.Source is not null) player.Play(); }
    internal void SeekBy(double seconds) { player.PlaybackSession.Position = TimeSpan.FromSeconds(Math.Max(0,Math.Min(player.PlaybackSession.NaturalDuration.TotalSeconds,player.PlaybackSession.Position.TotalSeconds+seconds))); }
    internal async Task OpenPreview() {
        await Initialize(true);
        if (disposed || info is null || info.Needs_chart) return;
        updating = true; bga.IsChecked = layer.IsChecked = false; VideoMode = false; updating = false;
        await Preview(false,false);
    }
    private Task RefreshPreview(bool restart=false) {
        if (!previewOnly || updating || IsBusy || disposed) return Task.CompletedTask;
        bool autoplay = player.PlaybackSession.PlaybackState == MediaPlaybackState.Playing;
        VideoMode = bga.IsChecked == true || layer.IsChecked == true;
        return Preview(VideoMode,autoplay,!restart);
    }
    private async Task<string?> Pick(params string[] extensions) {
        var picker = new FileOpenPicker(); foreach (var ext in extensions) picker.FileTypeFilter.Add(ext); initializePicker(picker);
        return (await picker.PickSingleFileAsync())?.Path;
    }
    private async Task<BackgroundCore> Connect(CancellationToken token) {
        token.ThrowIfCancellationRequested();
        if (core?.Backend.IsRunning == true) return core;
        var worker = new BackgroundCore(home,context); core = worker; workerChanged(worker);
        await worker.Open(); token.ThrowIfCancellationRequested(); return worker;
    }
    private void Stop() { core?.Dispose(); core = null; workerChanged(null); }
    public void Cancel() { revision++; player.Pause(); cancellation?.Cancel(); Stop(); SetBusy(false); status.Text = Tr("已取消"); }
    private static void Enable(UIElement element, bool value) {
        if (element is Control control) control.IsEnabled = value;
        else if (element is Panel panel) foreach (var child in panel.Children) Enable(child,value);
    }
    private void SetBusy(bool value) {
        IsBusy = value; mode.IsEnabled = !value; Enable(settings,!value); layerLoop.IsEnabled = !value && (layers.SelectedItem as LayerChoice)?.Id != -2;
        foreach (var button in commands.Children.OfType<Button>()) button.IsEnabled = !value;
        if (previewOnly) { bga.IsEnabled = !value && bgaPath.Length > 0; layer.IsEnabled = !value && layerPath.Length > 0; cancelButton.IsEnabled = value; cancelButton.Visibility = value ? Visibility.Visible : Visibility.Collapsed; }
        ring.IsActive = value; StateChanged?.Invoke();
    }
    private async Task Run(string message, Func<CancellationToken,Task> operation) {
        if (IsBusy || disposed) return;
        int current = ++revision; var job = new CancellationTokenSource(); cancellation = job; SetBusy(true); status.Text = Tr(message);
        using var registration = job.Token.Register(Stop);
        try { await operation(job.Token); if (current == revision && status.Text == Tr(message)) status.Text = Tr("就绪"); }
        catch (Exception ex) { if (current == revision && !disposed) status.Text = Tr(ex.Message); if (core?.Backend.IsRunning == false) Stop(); }
        finally { if (current == revision && !disposed) SetBusy(false); if (cancellation == job) cancellation = null; job.Dispose(); }
    }
    private Dictionary<string,object> Arguments() {
        var args = new Dictionary<string,object>();
        if (banks.SelectedItem is SongBank bank) args["bank"] = bank.Id;
        if (charts.SelectedItem is SongChart chart) args["chart"] = chart.Id;
        if (chartPath is not null) args["chart_path"] = chartPath;
        return args;
    }
    private Dictionary<string,object> Options() {
        var sizes = new[] { "480p","720p","1080p","720p-portrait","1080p-portrait" };
        var args = new Dictionary<string,object> { ["resolution"] = sizes[Math.Max(0,resolution.SelectedIndex)], ["fps"] = fps.SelectedIndex == 1 ? 120 : 60,
            ["renderer"] = renderMode, ["bga_offset"] = double.IsFinite(bgaOffset.Value) ? bgaOffset.Value : 0,
            ["layer_offset"] = double.IsFinite(layerOffset.Value) ? layerOffset.Value : 0,
            ["bga_loop"] = bgaLoop.IsChecked == true, ["layer_loop"] = layerLoop.IsChecked == true, ["layer_black_key"] = blackKey.IsChecked == true,
            ["layer_animation"] = (layers.SelectedItem as LayerChoice)?.Id ?? -1,
            ["layer_state"]=(layerState.SelectedItem as LayerState)?.Id ?? "clear",["layer_gauge"]=layerGauge.Value };
        if (bga.IsChecked == true && bgaPath.Length > 0) args["bga_path"] = bgaPath;
        if (layer.IsChecked == true && layerPath.Length > 0) args["layer_path"] = layerPath;
        return args;
    }
    private async Task OpenSong(BackgroundCore worker, string path, string? workingPath = null) {
        player.Pause(); player.Source = null; await worker.OpenSong(path,workingPath);
        context = context with { Path = workingPath ?? path, Context_path = path }; songPath = path; chartPath = null; info = null;
        updating = true; banks.ItemsSource = null; charts.ItemsSource = null; updating = false;
    }
    public Task Initialize(bool audio) => Run("正在读取音频…",async token => {
        if (originalAsset is not null && audioBackend is not null) {
            originalInfo = await audioBackend.Request<AudioExportInfo>("audio_export_info",new() { ["asset"] = originalAsset.Id });
            originalLabel.Text = Tr(originalAsset.Name + "\n原格式：" + System.IO.Path.GetExtension(originalInfo.Name).TrimStart('.').ToUpperInvariant());
            bool voiceBank=System.IO.Path.GetExtension(context.Context_path).Equals(".s3p",StringComparison.OrdinalIgnoreCase) && !File.Exists(System.IO.Path.ChangeExtension(context.Context_path,".1"));
            updating = true; audioSource.Visibility = audio ? Visibility.Visible : Visibility.Collapsed; audioSource.SelectedIndex = audio && !voiceBank ? 0 : 1; updating = false;
            UpdateAudioMode();
            if (OriginalAudio) return;
        }
        var worker = await Connect(token);
        if (!audio) {
            var related = await worker.Request<RelatedSongs>("related_songs");
            updating = true; songs.ItemsSource = related.Songs; songs.SelectedItem = related.Songs.FirstOrDefault(); songs.Visibility = related.Songs.Count > 0 ? Visibility.Visible : Visibility.Collapsed; updating = false;
            if (related.Songs.FirstOrDefault() is not RelatedSong first) throw new InvalidOperationException("未找到对应乐曲，请选择音频包");
            await OpenSong(worker,first.Path,first.Working_path);
        }
        await LoadInfo(worker,true);
    });
    private async Task LoadInfo(BackgroundCore worker, bool visuals) {
        int selected = (charts.SelectedItem as SongChart)?.Id ?? 1;
        info = await worker.Request<SongInfo>("song_info",Arguments());
        updating = true;
        try { banks.ItemsSource = info.Banks; banks.SelectedItem = info.Banks.FirstOrDefault(b => b.Id == info.Bank); charts.ItemsSource = info.Charts; charts.SelectedItem = info.Charts.FirstOrDefault(c => c.Id == selected) ?? info.Charts.FirstOrDefault(); }
        finally { updating = false; }
        if (visuals) {
            var result = await worker.Request<SongVisuals>("song_visuals");
            Title = Tr(string.IsNullOrEmpty(result.Title) ? "乐曲" : result.Title); StateChanged?.Invoke();
            bgaPath = result.Bga_path ?? ""; layerPath = result.Layer_path ?? "";
            updating = true; bgaName.Text = bgaPath; layerName.Text = layerPath; updating = false;
            bga.IsChecked = bgaPath.Length > 0; layer.IsChecked = layerPath.Length > 0; bgaOffset.Value = result.Bga_offset; layerOffset.Value = 0;
            bgaLoop.IsChecked = !System.IO.Path.GetFileNameWithoutExtension(bgaPath).All(char.IsDigit);
            preferredLayer = result.Layer_animation; loadedLayerPath = ""; layers.ItemsSource = null; layers.Visibility = Visibility.Collapsed;
            if (VideoMode) await LoadLayer(worker,preferredLayer);
        }
        if (info.Needs_chart) status.Text = Tr("需要配套 .1 谱面");
    }
    private async Task LoadLayer(BackgroundCore worker, string preferred = "") {
        if (layerPath.Length == 0) { layers.ItemsSource = null; layers.Visibility = Visibility.Collapsed; return; }
        var result = await worker.Request<LayerChoices>("layer_info",new() { ["path"] = layerPath, ["preferred"] = preferred });
        layers.ItemsSource = result.Animations; layers.SelectedItem = result.Animations.FirstOrDefault(a => a.Id == result.Selected); layers.Visibility = result.Animations.Count > 0 ? Visibility.Visible : Visibility.Collapsed; loadedLayerPath = layerPath;
        string state=(layerState.SelectedItem as LayerState)?.Id ?? "clear";
        layerState.ItemsSource=result.States;layerState.SelectedItem=result.States?.FirstOrDefault(s=>s.Id==state) ?? result.States?.FirstOrDefault();
        hasLayerGauge=result.Gauge;UpdateStateControls();
    }
    private void UpdateStateControls() {
        bool automatic=(layers.SelectedItem as LayerChoice)?.Id == -2;
        layerState.Visibility=automatic && layerState.ItemsSource is List<LayerState> states && states.Count>0 ? Visibility.Visible : Visibility.Collapsed;
        layerGauge.Visibility=automatic && hasLayerGauge?Visibility.Visible:Visibility.Collapsed;
    }
    private void UpdateBGAPath() {
        string path=InputPath(bgaName.Text);if(path==bgaPath)return;
        bgaPath=path;bga.IsChecked=path.Length>0;
    }
    private void UpdateLayerPath() {
        string path=InputPath(layerName.Text);if(path==layerPath)return;
        layerPath=path;layer.IsChecked=path.Length>0;
        preferredLayer=loadedLayerPath="";layers.ItemsSource=null;layers.Visibility=Visibility.Collapsed;
    }
    private async Task<Dictionary<string,object>> VideoOptions(BackgroundCore worker) {
        UpdateBGAPath();UpdateLayerPath();
        if (layer.IsChecked == true && layerPath.Length > 0 && (loadedLayerPath != layerPath || layers.SelectedItem is null)) await LoadLayer(worker,preferredLayer);
        return Options();
    }
    public Task Preview(bool movie = false, bool autoplay = true, bool preservePosition = false) => Run(movie ? "正在准备视频…" : "正在准备音频…",async token => {
        var position = preservePosition ? player.PlaybackSession.Position : TimeSpan.Zero;
        if (OriginalAudio && originalAsset is not null && audioBackend is not null) {
            var preview = await audioBackend.Request<PreviewReply>("asset",new() { ["asset"] = originalAsset.Id });
            token.ThrowIfCancellationRequested();
            if (preview.Path is null) throw new IOException("预览未生成");
            player.Source = MediaSource.CreateFromUri(new Uri(preview.Path)); player.Play(); status.Text = Tr("播放中"); return;
        }
        player.Pause(); var worker = await Connect(token); var args = Arguments(); if (movie) { var options=await VideoOptions(worker);options["preview"]=true;args["options"]=options; }
        var result = await worker.Request<PreviewReply>(movie ? "prepare_song_movie" : "prepare_song",args);
        token.ThrowIfCancellationRequested();
        if (result.Path is null) throw new IOException("预览未生成");
        pendingPosition = position > TimeSpan.Zero ? position : null;
        player.Source = MediaSource.CreateFromUri(new Uri(result.Path)); if (autoplay) player.Play();
        status.Text = Tr(autoplay ? "播放中" : "就绪");
    });
    public Task Export(string target, bool movie) => Run(movie ? "正在合成 MP4…" : "正在合成歌曲…",async token => {
        if (OriginalAudio && originalAsset is not null && audioBackend is not null) {
            await audioBackend.Request<ExportReply>("export",new() { ["kind"] = "audio_original", ["asset"] = originalAsset.Id, ["target"] = target });
            token.ThrowIfCancellationRequested(); status.Text = Tr("已导出：" + target); return;
        }
        var worker = await Connect(token); var args = Arguments(); if (movie) args["options"] = await VideoOptions(worker); args["target"] = target;
        await worker.Request<ExportReply>(movie ? "export_song_movie" : "export_song",args); token.ThrowIfCancellationRequested(); status.Text = Tr("已导出：" + target);
    });
    private async Task ChooseExport(bool movie) {
        var picker = new FolderPicker(); picker.FileTypeFilter.Add("*"); initializePicker(picker);
        var folder = await picker.PickSingleFolderAsync(); if (folder is null) return;
        string chart = (charts.SelectedItem as SongChart)?.Name.Replace(' ','_') ?? "song";
        string name = OriginalAudio ? originalInfo!.Name : System.IO.Path.GetFileNameWithoutExtension(songPath) + "_" + chart + (movie ? ".mp4" : ".wav");
        var target = System.IO.Path.Combine(folder.Path,name); await Export(target,movie);
    }
    public void Dispose() { if (disposed) return; disposed = true; Cancel(); video.SetMediaPlayer(null); player.Source = null; player.Dispose(); }
}

public sealed partial class MainWindow
{
    private static bool IsSongBank(string path) => System.IO.Path.GetExtension(path).ToLowerInvariant() is ".2dx" or ".s3p";
    private async void SongExport_Click(object sender, RoutedEventArgs e)
    {
        if (working || package is null) return;
        Pause();
        try {
            var context = await Request<ExportContext>("export_context");
            using var pane = new SongStudioPane(home,context,RenderMode,InitializePicker,worker => mediaJob = worker,backend,asset);
            var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = Tr("音频/视频导出"), Content = Tr(pane), CloseButtonText = Tr("关闭" )};
            dialog.Resources["ContentDialogMaxWidth"] = 760.0;
            pane.StateChanged += () => { dialog.CloseButtonText = Tr(pane.IsBusy ? "取消处理" : "关闭"); };
            dialog.CloseButtonClick += (_, args) => { if (pane.IsBusy) { args.Cancel = true; pane.Cancel(); } };
            mediaDialogOpen = true;
            try {
                var shown = dialog.ShowAsync();
                bool audio = IsSongBank(package.Path) || package.Assets.Any(a => a.Kind == "音频包" && IsSongBank(a.Name));
                await pane.Initialize(audio); await shown;
            } finally { mediaDialogOpen = false; }
        } catch (Exception ex) { ShowError(ex); }
    }
}
