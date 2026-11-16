using static IIDXResourceViewer.I18n;
using System.Diagnostics;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Shapes;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;
using Windows.Media.Core;
using Windows.Media.Playback;
using Windows.Storage;
using Windows.Storage.Pickers;
using VirtualKey = Windows.System.VirtualKey;

namespace IIDXResourceViewer;

public sealed partial class MainWindow : Window
{
    private double AnimationPlaybackFps => FpsChoice.SelectedIndex == 1 ? 120.0 : 60.0;
    private string PreviewResolution => ResolutionChoice.SelectedIndex switch {
        1 => "1080p", 2 => "720p-portrait", 3 => "1080p-portrait", 4 => "480p", _ => "720p"
    };
    private string RenderMode => RendererChoice.SelectedIndex == 0 ? "cpu" : "gpu";
    private readonly Backend? backend;
    private readonly RawFrameReader rawFrames = new();
    private RendererInfo? rendererInfo;
    private readonly MediaPlayer player = new() { AutoPlay = false };
    private readonly DispatcherQueueTimer timer;
    private readonly string home;
    private List<FileEntry> files = new();
    private List<ContentItem> contents = new();
    private readonly HashSet<string> collapsedCategories = new();
    private PackageInfo? package;
    private AnimationItem? animation;
    private AssetItem? asset;
    private SongStudioPane? songPreviewPane;
    private int frame, generation, openGeneration;
    private int? pendingFrame;
    private bool ready, playing, rendering, updatingSlider, working, shuttingDown;
    private int baseFrame;
    private double startedAt;
    private double lastProgressUpdate;
    private int searchGeneration;
    private CancellationTokenSource? searchCancellation;
    private CatalogSearch? catalogIndex;
    private bool mediaDialogOpen;
    private bool localizing;
    private string previewKind = "empty";
    private static double Now => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;
    private double Speed => SpeedChoice.SelectedIndex switch { 1 => .5, 2 => 1, 3 => 1.5, 4 => 2, _ => .25 };

    public MainWindow()
    {
        if(Environment.GetCommandLineArgs().Contains("--self-test"))I18n.SetLanguage("zh-Hans",false);
        InitializeComponent();
        Title=Tr("BEMANI素材管理器");
        AppWindow.SetIcon(System.IO.Path.Combine(AppContext.BaseDirectory, "Application.ico"));
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1500, 940));
        home = Environment.GetEnvironmentVariable("IIDX_VIEWER_HOME") ?? AppContext.BaseDirectory;
        var args = Environment.GetCommandLineArgs();
        int homeIndex = Array.IndexOf(args, "--home");
        if (homeIndex >= 0 && homeIndex + 1 < args.Length) home = args[homeIndex + 1];
        try { backend = new Backend(home); } catch (Exception ex) { ShowError(ex); }
        Versions.ItemsSource = new[] { "全部版本" }; Versions.SelectedIndex = 0;
        Kinds.ItemsSource = new[] { "所有类型", "动画/图像资源", "音频类文件", "BGA" }; Kinds.SelectedIndex = 0;
        SpeedChoice.SelectedIndex = 2; BackgroundChoice.SelectedIndex = 0;
        Video.SetMediaPlayer(player);
        player.MediaEnded += (_, _) => DispatcherQueue.TryEnqueue(() => {
            if (shuttingDown) return;
            if (LoopChoice.IsChecked == true) { player.PlaybackSession.Position = TimeSpan.Zero; player.Play(); }
            else Pause();
        });
        player.MediaFailed += (_, e) => DispatcherQueue.TryEnqueue(() => { if (!shuttingDown) { Pause(); ShowError(new IOException(e.ErrorMessage)); } });
        player.MediaOpened += (_, _) => DispatcherQueue.TryEnqueue(() => { if (!shuttingDown) UpdatePreviewLayout(); });
        timer = DispatcherQueue.CreateTimer(); timer.Interval = TimeSpan.FromSeconds(1 / AnimationPlaybackFps);
        timer.Tick += Timer_Tick;
        Root.KeyboardAccelerators.Add(Accelerator(VirtualKey.Space, () => TogglePlay()));
        Root.KeyboardAccelerators.Add(Accelerator(VirtualKey.Left, () => Step(-1)));
        Root.KeyboardAccelerators.Add(Accelerator(VirtualKey.Right, () => Step(1)));
        Root.Loaded += async (_, _) => {
            if (backend is null) return;
            int openIndex = Array.IndexOf(args, "--open");
            if (openIndex >= 0 && openIndex + 1 < args.Length) await Open(args[openIndex + 1]);
            else {
                int rootIndex = Array.IndexOf(args, "--root");
                if (rootIndex >= 0 && rootIndex + 1 < args.Length) await Scan(args[rootIndex + 1]);
            }
            if (args.Contains("--self-test")) await RunSelfTest();
            else if(args.Contains("--live2d-self-test"))await LiveSelfTest();
        };
        Closed += (_, _) => {
            liveEditorWindow?.Close();
            shuttingDown = true; ready = false; generation++; openGeneration++; searchGeneration++;
            searchCancellation?.Cancel(); searchCancellation?.Dispose();
            timer.Stop(); Pause(); Video.SetMediaPlayer(null); player.Source = null;
            songPreviewPane?.Dispose(); player.Dispose(); rawFrames.Dispose(); mediaJob?.Dispose(); backend?.Dispose(); Backend.FinishCleanup();
        };
        LanguageChoice.SelectedIndex=I18n.English?1:0;
        ready = true;
        Root.Loaded+=(_,_)=>{ I18n.Apply(Root); RefreshPreviewLabel(); };
    }

    private void Language_Changed(object sender,SelectionChangedEventArgs e) {
        if(!ready)return;
        I18n.SetLanguage(LanguageChoice.SelectedIndex==1?"en":"zh-Hans");
        ApplyLanguage();
    }
    private void ApplyLanguage() {
        Title=Tr("BEMANI素材管理器");I18n.RefreshChoices(Root);I18n.Apply(Root);
        RefreshPreviewLabel();songPreviewPane?.RefreshLanguage();
        localizing=true;
        try {
            var shown=FilesList.ItemsSource;var selected=FilesList.SelectedItem;var content=ContentsList.SelectedItem;
            FilesList.ItemsSource=null;FilesList.ItemsSource=shown;FilesList.SelectedItem=selected;
            ContentsList.ItemsSource=null;FilterContents();ContentsList.SelectedItem=content;
        } finally { localizing=false; }
    }
    private void RefreshPreviewLabel() {
        if(previewKind=="song")PreviewTitle.Text=Tr("完整乐曲预览");
        else if(package is null)PreviewTitle.Text=Tr("选择素材");
        if(package is null)PackageTitle.Text=Tr("包内素材");
    }

    private KeyboardAccelerator Accelerator(VirtualKey key, Action action)
    {
        var accelerator = new KeyboardAccelerator { Key = key };
        accelerator.Invoked += (_, e) => { if (CanUsePlaybackShortcut()) { action(); e.Handled = true; } };
        return accelerator;
    }
    private bool CanUsePlaybackShortcut()
    {
        if (mediaDialogOpen || Root.XamlRoot is null) return false;
        var focused = FocusManager.GetFocusedElement(Root.XamlRoot) as DependencyObject;
        for (var node = focused; node is not null; node = VisualTreeHelper.GetParent(node)) {
            if (node is TextBox or RichEditBox or PasswordBox or AutoSuggestBox or ComboBox or Slider or TextBlock { IsTextSelectionEnabled: true }) return false;
            if (node is not UIElement) break;
        }
        return true;
    }
    private Task<T> Request<T>(string method, Dictionary<string, object>? args = null) =>
        backend?.Request<T>(method, args) ?? Task.FromException<T>(new IOException("解析核心未启动，请运行 start_windows.bat。"));

    private void Busy(bool value, string? message = null)
    {
        working = value; BusyRing.IsActive = value;
        ExportMenu.IsEnabled = !value; FilesList.IsEnabled = !value; ContentsList.IsEnabled = !value;
        UpdateEditing();
        if (message is not null) Status.Text = Tr(message);
    }
    private void Warn(IEnumerable<string>? messages)
    {
        string text = string.Join("\n", messages?.Take(4) ?? Enumerable.Empty<string>());
        if (WarningText.Text == text) return;
        WarningText.Text = Tr(text);
        Notice.Visibility = string.IsNullOrEmpty(WarningText.Text) ? Visibility.Collapsed : Visibility.Visible;
    }
    private void ShowError(Exception ex) { Status.Text = Tr(ex.Message); Warn(new[] { ex.Message }); }
    private void UpdateEditing()
    {
        AnimationInfoButton.IsEnabled = animation is not null && !working;
        LayersButton.IsEnabled = animation is not null && !working;
        EditMenu.IsEnabled = package is not null && !working && package.Format!="live2d";
        ReplaceAssetMenuItem.IsEnabled = asset?.Replaceable == true && !working;
        UndoReplacementMenuItem.IsEnabled = package?.Can_undo == true && !working;
        SaveReplacementMenuItem.IsEnabled = package?.Modified == true && !working;
        SongExportMenuItem.IsEnabled = !working && package is not null && package.Format!="live2d" &&
            (package.Path.EndsWith(".2dx", StringComparison.OrdinalIgnoreCase) || package.Assets.Any(a => a.Kind is "音频" or "音频包" or "视频") || package.Animations.Count > 0);
        MP4ExportMenuItem.IsEnabled = !working && (animation is not null || asset?.Kind == "视频");
        DraftIndicator.Text = Tr(package?.Modified == true ? $"副本 · {package.Change_count} 次修改" : "");
        DraftIndicator.Visibility = package?.Modified == true ? Visibility.Visible : Visibility.Collapsed;
    }
    private void ResetPreview()
    {
        songPreviewPane?.Dispose(); songPreviewPane = null; SongPreviewHost.Content = null; SongPreviewHost.Visibility = Visibility.Collapsed; MainPlaybackControls.Visibility = Visibility.Visible;
        ClearLayerSelection(); collapsedLayers.Clear(); pickToken = null;
        LayersFlyout.Hide(); layerRows.Clear(); layerReport = null;
        Pause(); generation++; rendering = false; pendingFrame = null; frame = 0;
        player.Source = null; animation = null; asset = null; previewKind = "empty";
        UpdateClock();
        PreviewSettings.Visibility = Visibility.Collapsed;
        PreviewImage.Source = null; ImageScroll.Visibility = Video.Visibility = TextScroll.Visibility = Visibility.Collapsed;
        ImageScroll.ChangeView(0, 0, 1);
        EmptyState.Visibility = Visibility.Visible; EmptyTitle.Text = Tr("选择素材");
        Warn(null); SetSlider(0, 1); TimeLabel.Text = Tr("0 / 0");
        UpdateEditing();
    }

    private async Task Scan(string path)
    {
        if (working) return;
        Busy(true, "正在扫描…"); Pause();
        try {
            var info = await Request<ScanReply>("scan", new() { ["root"] = path });
            files = info.Files;
            Versions.ItemsSource = new[] { "全部版本" }.Concat(files.Select(f => f.Version).Distinct().OrderBy(v => v)).ToList();
            Versions.SelectedIndex = 0; FilterFiles();
            Status.Text = Tr($"找到 {files.Count:N0} 个文件 · {info.Root}");
        } catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }
    private void FilterFiles() { if (ready) _ = FilterLibrary(false); }
    private async Task FilterLibrary(bool debounce)
    {
        if (!ready) return;
        int revision = ++searchGeneration;
        searchCancellation?.Cancel(); searchCancellation?.Dispose();
        var cancellation = new CancellationTokenSource(); searchCancellation = cancellation;
        var token = cancellation.Token;
        string query = FileSearch.Text, version = Versions.SelectedItem as string ?? "全部版本", kind = Kinds.SelectedItem as string ?? "所有类型";
        var source = files;
        try {
            if (debounce) await Task.Delay(65,token);
            var filtered = await Task.Run(() => {
                var index = Volatile.Read(ref catalogIndex);
                if (index is null || !ReferenceEquals(index.Source,source)) {
                    index = new CatalogSearch(source,token); token.ThrowIfCancellationRequested();
                    Interlocked.Exchange(ref catalogIndex,index);
                }
                return index.Search(version,kind,query,token);
            },token);
            if (token.IsCancellationRequested || revision != searchGeneration || shuttingDown) return;
            if (!ReferenceEquals(FilesList.ItemsSource,filtered)) FilesList.ItemsSource = filtered;
            FileCount.Text = Tr($"{filtered.Count:N0} / {source.Count:N0} 个文件");
        } catch (OperationCanceledException) { }
        catch (Exception ex) { if (revision == searchGeneration && !shuttingDown) ShowError(ex); }
    }
    private async void Filter_Changed(object sender, TextChangedEventArgs e) => await FilterLibrary(true);
    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e) => FilterFiles();
    private void AssetSearch_Changed(object sender, TextChangedEventArgs e) => FilterContents();
    private void FilterContents()
    {
        if (!ready) return;
        var matching = contents.Where(c => c.Name.Contains(AssetSearch.Text, StringComparison.OrdinalIgnoreCase)).ToList();
        if (AssetSearch.Text.Length > 0) { ContentsList.ItemsSource = matching; return; }
        var rows = new List<ContentItem>();
        foreach (var group in matching.GroupBy(c => c.Type == "animation" ? "动画" : c.Group)) {
            bool expanded = !collapsedCategories.Contains(group.Key);
            rows.Add(new ContentItem($"{group.Key} · {group.Count()}", "", "header", -1, group.Key, true, expanded));
            if (expanded) rows.AddRange(group);
        }
        ContentsList.ItemsSource = rows;
    }

    private async Task Open(string path)
    {
        if (working) return;
        ResetPreview(); package = null; contents.Clear(); ContentsList.ItemsSource = null;
        Busy(true, "正在读取资源…");
        int token = ++openGeneration;
        try {
            var info = await Request<PackageInfo>("open", new() { ["path"] = path });
            if (token == openGeneration) Accept(info);
        } catch (Exception ex) { if (token == openGeneration) ShowError(ex); }
        finally { Busy(false); }
    }
    private void Accept(PackageInfo info)
    {
        package = info; PackageTitle.Text = Tr(info.DisplayTitle);
        ToolTipService.SetToolTip(PackageTitle,info.DisplayTitle);
        LiveEditorButton.Visibility=info.Format=="live2d"?Visibility.Visible:Visibility.Collapsed;
        AnimationInfoButton.Visibility=info.Format=="live2d"?Visibility.Collapsed:Visibility.Visible;
        EngineSummary.Text = Tr(info.Engine?.Label ?? "");
        ToolTipService.SetToolTip(EngineSummary, info.Engine?.Detail);
        contents = info.Animations.Select(a => new ContentItem(a.Name, $"动画 · {a.Length} 帧", "animation", a.Id))
            .Concat(info.Assets.Select(a => new ContentItem(a.Name, string.IsNullOrEmpty(a.Details) ? a.Kind : $"{a.Kind} · {a.Details}", "asset", a.Id, a.Kind))).ToList();
        if (info.Song_preview) contents.Insert(0,new ContentItem("完整乐曲预览","BGA · Layer","song",-1,"预览"));
        FilterContents(); Warn(info.Warnings); Status.Text = Tr($"{info.Assets.Count} 个素材 · {info.Animations.Count} 个动画");
        if (!string.IsNullOrEmpty(info.Edit_notice)) Status.Text = Tr(info.Edit_notice);
        UpdateEditing();
        // Selection is deferred so Open can finish its busy state first.
        var preferred = contents.FirstOrDefault(c => c.Type == "asset" && c.Name == info.Selected_asset)
            ?? contents.FirstOrDefault(c => c.Type == "animation")
            ?? contents.FirstOrDefault(c => c.Type == "asset" && info.Assets.Any(a => a.Id == c.Index && a.Kind is "图片" or "音频" or "视频"))
            ?? contents.FirstOrDefault();
        DispatcherQueue.TryEnqueue(() => {
            if (preferred is null || package != info) return;
            if (collapsedCategories.Remove(preferred.Type == "animation" ? "动画" : preferred.Group)) FilterContents();
            ContentsList.SelectedItem = preferred;
        });
    }
    private async void Files_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!localizing && FilesList.SelectedItem is FileEntry f) await Open(f.Path);
    }
    private async void Contents_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (localizing || working || package is null || ContentsList.SelectedItem is not ContentItem item) return;
        if (item.IsHeader) {
            if (!collapsedCategories.Add(item.Group)) collapsedCategories.Remove(item.Group);
            FilterContents(); return;
        }
        ResetPreview(); int token = generation; PreviewTitle.Text = item.Type=="song" ? Tr(item.Name) : item.Name;
        if (item.Type == "song") {
            try {
            previewKind = "song"; PreviewDetail.Text = Tr(""); EmptyState.Visibility = Visibility.Collapsed; MainPlaybackControls.Visibility = Visibility.Collapsed;
            var context = await Request<ExportContext>("export_context");
            if (token != generation) return;
            var pane = new SongStudioPane(home,context,RenderMode,InitializePicker,_ => {},previewOnly:true);
            songPreviewPane = pane; SongPreviewHost.Content = Tr(pane); SongPreviewHost.Visibility = Visibility.Visible; pane.PreviewHeight(SongPreviewHost.ActualHeight);
            await pane.OpenPreview(); UpdateEditing();
            } catch (Exception ex) { if (token == generation) ShowError(ex); }
            return;
        }
        if (item.Type == "animation") {
            animation = package.Animations.First(a => a.Id == item.Index);
            PreviewDetail.Text = Tr(""); PreviewSettings.Visibility = Visibility.Visible;
            ToolTipService.SetToolTip(ResolutionChoice, $"原始尺寸 {animation.Width} × {animation.Height}；小素材不放大");
            SetSlider(0, Math.Max(1, animation.Length - 1));
            await Render(0);
            UpdateEditing();
            return;
        }
        asset = package.Assets.First(a => a.Id == item.Index);
        PreviewDetail.Text = Tr(string.IsNullOrEmpty(asset.Details) ? asset.Kind : asset.Details);
        Busy(true, "正在读取素材…");
        try {
            var preview = await Request<PreviewReply>("asset", new() { ["asset"] = asset.Id });
            if (token != generation) return;
            if (preview.Kind == "tracks" && preview.Assets is not null) {
                var existing = package.Assets.Select(a => a.Id).ToHashSet();
                var added = preview.Assets.Where(a => !existing.Contains(a.Id)).ToList();
                package.Assets.AddRange(added);
                var tracks = added.Select(a => new ContentItem(a.Name, "音频", "asset", a.Id, "音频")).ToList();
                contents.AddRange(tracks); AssetSearch.Text = Tr(""); FilterContents();
                DispatcherQueue.TryEnqueue(() => {
                    if (preview.Assets.Count > 0) ContentsList.SelectedItem = contents.FirstOrDefault(c => c.Type == "asset" && c.Index == preview.Assets[0].Id);
                });
            } else if (preview.Kind == "package" && preview.Package is not null) Accept(preview.Package);
            else if (preview.Kind == "image" && preview.Path is not null) await DisplayImage(preview.Path, token);
            else if (preview.Kind is "audio" or "video" && preview.Path is not null) {
                previewKind = preview.Kind; player.Source = MediaSource.CreateFromUri(new Uri(preview.Path));
                player.PlaybackSession.PlaybackRate = Speed;
                Video.Visibility = preview.Kind == "video" ? Visibility.Visible : Visibility.Collapsed;
                UpdateClock();
                UpdatePreviewLayout();
                EmptyState.Visibility = preview.Kind == "audio" ? Visibility.Visible : Visibility.Collapsed;
                EmptyIcon.Glyph = "\uE189"; EmptyTitle.Text = Tr("空格播放 / 暂停");
            } else {
                previewKind = "text"; TextPreview.Text = preview.Text ?? ""; TextScroll.Visibility = Visibility.Visible; EmptyState.Visibility = Visibility.Collapsed;
            }
            Warn(preview.Warnings); Status.Text = Tr(string.IsNullOrEmpty(package?.Edit_notice) ? "就绪" : package.Edit_notice);
        } catch (Exception ex) { if (token == generation) ShowError(ex); }
        finally { Busy(false); }
    }

    private async Task DisplayImage(string path, int token)
    {
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenReadAsync();
        var bitmap = new BitmapImage(); await bitmap.SetSourceAsync(stream);
        if (token != generation) return;
        PreviewImage.Source = bitmap; previewKind = "image";
        ImageScroll.Visibility = Visibility.Visible; EmptyState.Visibility = Visibility.Collapsed;
        UpdatePreviewLayout();
    }
    private async Task Render(int desired)
    {
        if (pickingLayer) return;
        if (animation is null) return;
        if (desired != frame) { selectedLayerBounds = null; UpdateLayerOutline(); }
        if (rendering) { pendingFrame = desired; return; }
        int token = generation; rendering = true;
        try {
            var preview = await Request<PreviewReply>("render", new() { ["animation"] = animation.Id, ["frame"] = desired, ["transport"] = "shared",
                ["resolution"] = PreviewResolution, ["renderer"] = RenderMode });
            if (token != generation) return;
            if (pendingFrame is int pending && pending != desired) return;
            if (preview.Kind == "raw") {
                var bitmap = rawFrames.Read(preview);
                if (!ReferenceEquals(PreviewImage.Source, bitmap)) {
                    PreviewImage.Source = bitmap; previewKind = "image";
                    ImageScroll.Visibility = Visibility.Visible; EmptyState.Visibility = Visibility.Collapsed;
                    UpdatePreviewLayout();
                }
            } else if (preview.Path is not null) await DisplayImage(preview.Path, token);
            if (rendererInfo != preview.Renderer) {
                rendererInfo = preview.Renderer;
                ToolTipService.SetToolTip(RendererChoice, rendererInfo?.Device ?? rendererInfo?.Reason ?? "CPU");
            }
            if (token != generation || animation is null) return;
            frame = preview.Frame ?? desired;
            if(liveEditor is {} live)live.Sync(PreviewImage.Source,frame,animation.Length);
            if (!playing || Now - lastProgressUpdate >= 1.0 / 30) {
                lastProgressUpdate = Now;
                SetSlider(frame, Math.Max(1, animation.Length - 1));
                TimeLabel.Text = Tr($"{frame + 1} / {animation.Length}");
            }
            Warn(preview.Warnings);
        } catch (Exception ex) { if (token == generation) { Pause(); ShowError(ex); } }
        finally {
            if (token == generation) {
                rendering = false;
                if (pendingFrame is int next) {
                    pendingFrame = null;
                    if (next != frame) DispatcherQueue.TryEnqueue(async () => await Render(next));
                }
            }
        }
    }

    private void SetSlider(double value, double maximum)
    {
        updatingSlider = true;
        if (TimelineSlider.Maximum != maximum) TimelineSlider.Maximum = maximum;
        if (TimelineSlider.Value != value) TimelineSlider.Value = value;
        updatingSlider = false;
    }
    private void TogglePlay()
    {
        if (working) return;
        if (songPreviewPane is not null) { songPreviewPane.TogglePlayback(); return; }
        if (playing) { Pause(); return; }
        selectedLayerBounds = null; UpdateLayerOutline();
        if (animation is not null) {
            if (frame >= animation.Length - 1) frame = 0;
            baseFrame = frame; startedAt = Now;
        } else if (previewKind is "audio" or "video") {
            if (player.PlaybackSession.Position >= player.PlaybackSession.NaturalDuration) player.PlaybackSession.Position = TimeSpan.Zero;
            player.Play(); player.PlaybackSession.PlaybackRate = Speed;
        } else return;
        playing = true; PlayIcon.Symbol = Symbol.Pause; Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(PlayButton, "暂停"); UpdateClock();
    }
    private void Pause(bool updateProgress = true)
    {
        songPreviewPane?.Player.Pause();
        playing = false; player.Pause(); PlayIcon.Symbol = Symbol.Play; Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(PlayButton, "播放"); UpdateClock();
        if (updateProgress && animation is not null) {
            SetSlider(frame, Math.Max(1, animation.Length - 1));
            TimeLabel.Text = Tr($"{frame + 1} / {animation.Length}");
        }
    }
    private void UpdateClock()
    {
        if (timer is null) return;
        if (shuttingDown || !playing && previewKind is not ("audio" or "video")) { timer.Stop(); return; }
        timer.Interval = TimeSpan.FromSeconds(animation is not null && playing ? 1 / AnimationPlaybackFps : playing ? 1.0 / 30 : 1.0 / 10);
        if (!timer.IsRunning) timer.Start();
    }
    private async void Timer_Tick(DispatcherQueueTimer sender, object args)
    {
        if (shuttingDown) return;
        if (animation is null) {
            if (previewKind is "audio" or "video") {
                var session = player.PlaybackSession;
                SetSlider(session.Position.TotalSeconds, Math.Max(1, session.NaturalDuration.TotalSeconds));
                string label = $"{session.Position.TotalSeconds:0.0} / {session.NaturalDuration.TotalSeconds:0.0}s";
                if (TimeLabel.Text != label) TimeLabel.Text = Tr(label);
            }
            return;
        }
        if (!playing || rendering || working) return;
        int desired = baseFrame + (int)((Now - startedAt) * AnimationPlaybackFps * Speed);
        if (desired >= animation.Length) {
            if (LoopChoice.IsChecked == true) desired %= animation.Length;
            else { desired = animation.Length - 1; Pause(); }
        }
        if (desired != frame) await Render(desired);
    }
    private async void Step(int delta)
    {
        if (songPreviewPane is not null) { songPreviewPane.SeekBy(delta*5); return; }
        if (animation is null || working) return;
        Pause(); await Render(Math.Clamp(frame + delta, 0, animation.Length - 1));
    }
    private void Play_Click(object sender, RoutedEventArgs e) => TogglePlay();
    private void SongPreview_SizeChanged(object sender, SizeChangedEventArgs e) => songPreviewPane?.PreviewHeight(e.NewSize.Height);
    private void Previous_Click(object sender, RoutedEventArgs e) => Step(-1);
    private void Next_Click(object sender, RoutedEventArgs e) => Step(1);
    private async void Timeline_Changed(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (!ready || updatingSlider || working) return;
        if (animation is not null) { Pause(false); await Render((int)e.NewValue); }
        else if (previewKind is "audio" or "video") player.PlaybackSession.Position = TimeSpan.FromSeconds(e.NewValue);
    }
    private void Speed_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!ready) return;
        baseFrame = frame; startedAt = Now; player.PlaybackSession.PlaybackRate = Speed;
    }

    private void Fps_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!ready) return;
        baseFrame = frame; startedAt = Now;
        UpdateClock();
    }

    private async void RenderSettings_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!ready) return;
        generation++; rendering = false; pendingFrame = null;
        baseFrame = frame; startedAt = Now;
        if (animation is not null) await Render(frame);
    }

    private void InitializePicker(object picker) => WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
    private async void AnimationInfo_Click(object sender, RoutedEventArgs e)
    {
        if (animation is null || working) return;
        Pause(); Busy(true, "正在读取动画信息…");
        try {
            var reply = await Request<PreviewReply>("inspect_animation", new() { ["animation"] = animation.Id });
            var text = new TextBlock { Text = Tr(reply.Text ?? ""), IsTextSelectionEnabled = true,
                FontFamily = new FontFamily("Consolas"), FontSize = 13, TextWrapping = TextWrapping.NoWrap };
            var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = Tr("动画信息"), CloseButtonText = Tr("关闭"),
                Content = Tr(new ScrollViewer { Content = Tr(text), MaxHeight = 600, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto } )};
            await dialog.ShowAsync(); Status.Text = Tr("就绪");
        } catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }
    private async void Folder_Click(object sender, RoutedEventArgs e)
    {
        if (working) return;
        var picker = new FolderPicker(); picker.FileTypeFilter.Add("*"); InitializePicker(picker);
        var folder = await picker.PickSingleFolderAsync(); if (folder is not null) await Scan(folder.Path);
    }
    private async void File_Click(object sender, RoutedEventArgs e)
    {
        if (working) return;
        var picker = new FileOpenPicker();
        picker.FileTypeFilter.Add("*");
        InitializePicker(picker); var file = await picker.PickSingleFileAsync(); if (file is not null) await Open(file.Path);
    }
    private async void Export_Click(object sender, RoutedEventArgs e)
    {
        if (working || package is null || sender is not MenuFlyoutItem menu) return;
        string kind = menu.Tag.ToString()!;
        if (kind is "frame" or "webp" or "sequence" && animation is null) { Status.Text = Tr("请先选择动画"); return; }
        if (kind == "asset" && asset is null) { Status.Text = Tr("请先选择素材"); return; }
        Pause();
        int token = generation;
        var selectedAsset = asset;
        var selectedAnimation = animation;
        // Choose a parent and a new child name, avoiding FileSavePicker's creation of
        // an empty file before the service can enforce exclusive, read-only exports.
        var folderPicker = new FolderPicker(); folderPicker.FileTypeFilter.Add("*"); InitializePicker(folderPicker);
        var folder = await folderPicker.PickSingleFolderAsync(); if (folder is null) return;
        if (token != generation) return;
        string name = kind is "textures" or "audio" ? System.IO.Path.GetFileNameWithoutExtension(package.Path) + "_" + kind : PreviewTitle.Text;
        name = string.Join("_", name.Split(System.IO.Path.GetInvalidFileNameChars()));
        name += kind switch { "frame" => $"_{frame:000000}.png", "webp" => ".webp", "sequence" => "_frames", _ => "" };
        if (kind == "asset") name = System.IO.Path.GetFileName(selectedAsset!.Name);
        string target = System.IO.Path.Combine(folder.Path, name);
        Busy(true, "正在导出…");
        try {
            var args = new Dictionary<string, object> { ["kind"] = kind, ["target"] = target, ["frame"] = frame };
            if (selectedAnimation is not null) args["animation"] = selectedAnimation.Id;
            if (selectedAsset is not null) args["asset"] = selectedAsset.Id;
            var reply = await Request<ExportReply>("export", args); Status.Text = Tr("已导出：" + reply.Path);
        } catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }

    private async void Replace_Click(object sender, RoutedEventArgs e)
    {
        if (working || package is null || sender is not MenuFlyoutItem item) return;
        bool whole = item.Tag?.ToString() == "file";
        if (!whole && asset?.Replaceable != true) { Status.Text = Tr("请选择可替换的素材"); return; }
        Pause();
        var picker = new FileOpenPicker(); picker.FileTypeFilter.Add("*"); InitializePicker(picker);
        var source = await picker.PickSingleFileAsync();
        if (source is not null) await ApplyReplacement(source.Path, whole);
    }

    private async Task ApplyReplacement(string source, bool whole)
    {
        if (working || package is null || !whole && asset?.Replaceable != true) return;
        var args = new Dictionary<string, object> { ["scope"] = whole ? "file" : "asset", ["source"] = source };
        if (asset is not null) args["asset"] = asset.Id;
        Pause(); generation++; rendering = false; pendingFrame = null;
        Busy(true, "正在替换…");
        try {
            var result = await Request<PackageInfo>("replace", args);
            ResetPreview();
            Accept(result); Status.Text = Tr(result.Edit_notice ?? "已替换");
        } catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }

    private async void UndoReplacement_Click(object sender, RoutedEventArgs e)
    {
        if (working || package?.Can_undo != true) return;
        Pause(); generation++; rendering = false; pendingFrame = null;
        Busy(true, "正在撤销…");
        try { var result = await Request<PackageInfo>("undo_replace"); ResetPreview(); Accept(result); }
        catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }

    private async void SaveReplacement_Click(object sender, RoutedEventArgs e)
    {
        if (working || package?.Modified != true) return;
        Pause();
        var picker = new FolderPicker(); picker.FileTypeFilter.Add("*"); InitializePicker(picker);
        var parent = await picker.PickSingleFolderAsync(); if (parent is null) return;
        string name = System.IO.Path.GetFileNameWithoutExtension(package.Path) + "_替换_" + DateTime.Now.ToString("yyyyMMdd_HHmmss");
        string target = System.IO.Path.Combine(parent.Path, name);
        for (int i = 2; Directory.Exists(target) || File.Exists(target); i++) target = System.IO.Path.Combine(parent.Path, name + "_" + i);
        Busy(true, "正在保存…");
        try {
            var reply = await Request<ExportReply>("save_replacement", new() { ["target"] = target });
            Status.Text = Tr("已保存：" + reply.Path);
        } catch (Exception ex) { ShowError(ex); }
        finally { Busy(false); }
    }

    private static Windows.Foundation.Size PreviewSize(double sourceWidth, double sourceHeight, double width, double height, string mode)
    {
        sourceWidth = Math.Max(1, sourceWidth); sourceHeight = Math.Max(1, sourceHeight);
        width = Math.Max(1, width); height = Math.Max(1, height);
        if (mode == "1:1") return new(sourceWidth, sourceHeight);
        if (mode == "拉伸") return new(width, height);
        double ratio = mode == "16:9" ? 16.0 / 9 : mode == "4:3" ? 4.0 / 3 : sourceWidth / sourceHeight;
        double fittedWidth = Math.Min(width, height * ratio);
        return new(fittedWidth, fittedWidth / ratio);
    }
    private void UpdatePreviewLayout()
    {
        string mode = ScaleChoice.SelectedItem as string ?? "适应";
        double width = PreviewGrid.ActualWidth, height = PreviewGrid.ActualHeight;
        if (PreviewImage.Source is BitmapSource bitmap) {
            var size = PreviewSize(bitmap.PixelWidth, bitmap.PixelHeight, width, height, mode);
            if (PreviewImage.MaxWidth != double.PositiveInfinity) PreviewImage.MaxWidth = double.PositiveInfinity;
            if (PreviewImage.MaxHeight != double.PositiveInfinity) PreviewImage.MaxHeight = double.PositiveInfinity;
            if (PreviewImage.Stretch != Stretch.Fill) PreviewImage.Stretch = Stretch.Fill;
            if (PreviewImage.Width != size.Width) PreviewImage.Width = size.Width;
            if (PreviewImage.Height != size.Height) PreviewImage.Height = size.Height;
        }
        if (previewKind == "video") {
            var session = player.PlaybackSession;
            var size = PreviewSize(session.NaturalVideoWidth > 0 ? session.NaturalVideoWidth : 1920,
                session.NaturalVideoHeight > 0 ? session.NaturalVideoHeight : 1080, width, height, mode);
            if (Video.Width != size.Width) Video.Width = size.Width;
            if (Video.Height != size.Height) Video.Height = size.Height;
        }
        UpdateLayerOutline();
    }
    private void Scale_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!ready) return;
        UpdatePreviewLayout(); ImageScroll.ChangeView(0, 0, 1);
    }
    private void Preview_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        PreviewGrid.Clip = new RectangleGeometry { Rect = new Windows.Foundation.Rect(0, 0, Math.Max(0, e.NewSize.Width), Math.Max(0, e.NewSize.Height)) };
        if (ready) UpdatePreviewLayout();
    }
    private void Workspace_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        double available = Math.Max(0, e.NewSize.Width - 56);
        FileColumn.Width = new GridLength(Math.Clamp(available * 0.23, 170, 280));
        ContentColumn.Width = new GridLength(Math.Clamp(available * 0.24, 180, 280));
        if (ready) UpdatePreviewLayout();
    }
    private void Background_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!ready) return;
        string choice = BackgroundChoice.SelectedItem as string ?? "棋盘";
        Checker.Visibility = choice == "棋盘" ? Visibility.Visible : Visibility.Collapsed;
        PreviewGrid.Background = choice == "白色" ? new SolidColorBrush(Microsoft.UI.Colors.White) : choice == "黑色" ? new SolidColorBrush(Microsoft.UI.Colors.Black) : new SolidColorBrush(Windows.UI.Color.FromArgb(255, 240, 240, 240));
    }
    private void Checker_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        int width = Math.Clamp((int)Math.Ceiling(e.NewSize.Width),1,4096), height = Math.Clamp((int)Math.Ceiling(e.NewSize.Height),1,4096);
        var bitmap = new WriteableBitmap(width,height);
        var pixels = new byte[checked(width * height * 4)];
        for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
            int at = (y * width + x) * 4;
            byte gray = (byte)(((x / 16 + y / 16) & 1) == 0 ? 240 : 214);
            pixels[at] = pixels[at + 1] = pixels[at + 2] = gray; pixels[at + 3] = 255;
        }
        using (var stream = bitmap.PixelBuffer.AsStream()) stream.Write(pixels);
        bitmap.Invalidate();
        Checker.Children.Clear();
        Checker.Children.Add(new Image { Source = bitmap, Width = e.NewSize.Width, Height = e.NewSize.Height, Stretch = Stretch.Fill });
    }
    private async void Help_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new ContentDialog {
            XamlRoot = Root.XamlRoot, Title = Tr("BEMANI素材管理器")+" · 1.0.0 Beta", CloseButtonText = Tr("完成"),
            Content = Tr(new TextBlock { TextWrapping = TextWrapping.Wrap, Text = Tr("打开目录或文件，选择素材预览。\n空格播放 / 暂停，方向键逐帧，拖动进度条定位。\n导出支持 PNG、WebP、PNG 序列和原文件。\n\n替换后可预览、撤销；另存时选择新文件夹。\n\n支持 IFS、BIN/TXP2、IDX/GCZ。旧版素材需保留关联文件；部分效果可能不完整。" )}
        )};
        await dialog.ShowAsync();
    }
    private void Root_DragOver(object sender, DragEventArgs e) { e.AcceptedOperation = DataPackageOperation.Copy; }
    private async void Root_Drop(object sender, DragEventArgs e)
    {
        if (working || !e.DataView.Contains(StandardDataFormats.StorageItems)) return;
        var items = await e.DataView.GetStorageItemsAsync();
        if (items.FirstOrDefault() is StorageFolder folder) await Scan(folder.Path);
        else if (items.FirstOrDefault() is StorageFile file) await Open(file.Path);
    }
}
