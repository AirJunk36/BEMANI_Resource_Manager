using System.Diagnostics;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace IIDXResourceViewer;

public sealed partial class MainWindow
{
    private async Task<Dictionary<string,object>> RunMediaSmoke(string dataRoot, string testRoot)
    {
        string s3p = System.IO.Path.Combine(dataRoot,"34_S3PTest","data","sound","20032.s3p");
        await Open(s3p);
        await AwaitState(() => package is not null && !working && previewKind == "audio","S3P song bank");
        if (!SongExportMenuItem.IsEnabled) throw new Exception("S3P full-song command disabled");
        if (package?.Song_preview != true || !contents.Any(c => c.Type == "song")) throw new Exception("Main audio package lost complete-song preview row");
        ContentsList.SelectedItem = contents.First(c => c.Type == "song");
        await AwaitState(() => songPreviewPane is { IsBusy:false } && songPreviewPane.Player.PlaybackSession.NaturalDuration.TotalSeconds > 0,"main song preview");
        var localizedPane=songPreviewPane!;var localizedSource=localizedPane.Player.Source;
        I18n.SetLanguage("en",false);ApplyLanguage();
        if(PreviewTitle.Text!="Full song preview"||contents.First(c=>c.Type=="song").DisplayName!="Full song preview"||localizedPane.StatusText!="Ready")throw new Exception("Full song preview was not localized");
        if(!ReferenceEquals(localizedPane,songPreviewPane)||!ReferenceEquals(localizedSource,localizedPane.Player.Source))throw new Exception("Language change restarted song preview");
        Root.UpdateLayout();string songEnglish=System.IO.Path.Combine(testRoot,"song-preview-en.png");File.WriteAllBytes(songEnglish,Array.Empty<byte>());await CapturePreview(songEnglish);
        var stateVisibility=localizedPane.LayerStates.Visibility;var gaugeVisibility=localizedPane.LayerGauge.Visibility;
        localizedPane.LayerStates.Visibility=localizedPane.LayerGauge.Visibility=Visibility.Visible;
        foreach(double height in new[]{220.0,260.0,380.0}) {
            SongPreviewHost.Height=height;Root.UpdateLayout();
            var bar=localizedPane.CommandsPanel;
            var bounds=bar.TransformToVisual(SongPreviewHost).TransformBounds(new Windows.Foundation.Rect(0,0,bar.ActualWidth,bar.ActualHeight));
            if(bar.ActualHeight<25 || bounds.Bottom>SongPreviewHost.ActualHeight-10 || bounds.Top<0)throw new Exception("Song preview footer clipped at "+height);
            var videoBounds=localizedPane.PlayerPanel.TransformToVisual(SongPreviewHost).TransformBounds(new Windows.Foundation.Rect(0,0,localizedPane.PlayerPanel.ActualWidth,localizedPane.PlayerPanel.ActualHeight));
            if(videoBounds.Bottom>bounds.Top-8)throw new Exception("Video covers the preview footer at "+height);
        }
        SongPreviewHost.Height=double.NaN;localizedPane.LayerStates.Visibility=stateVisibility;localizedPane.LayerGauge.Visibility=gaugeVisibility;Root.UpdateLayout();
        I18n.SetLanguage("zh-Hans",false);ApplyLanguage();
        if(PreviewTitle.Text!="完整乐曲预览"||localizedPane.StatusText!="就绪")throw new Exception("Song preview did not restore Chinese");
        if (songPreviewPane!.BGAPlayback.IsChecked == true || songPreviewPane.LayerPlayback.IsChecked == true || !songPreviewPane.HasPreviewUI)
            throw new Exception("Main song preview did not default to audio");
        songPreviewPane.Player.PlaybackSession.Position = TimeSpan.FromSeconds(.5);
        songPreviewPane.BGAPlayback.IsChecked = true;
        await AwaitState(() => songPreviewPane is { IsBusy:false } && songPreviewPane.Player.PlaybackSession.NaturalVideoWidth > 0,"main BGA checkbox");
        if (songPreviewPane!.Player.PlaybackSession.PlaybackState == Windows.Media.Playback.MediaPlaybackState.Playing) throw new Exception("BGA checkbox started paused playback");
        songPreviewPane.LayerPlayback.IsChecked = true;
        await AwaitState(() => songPreviewPane is { IsBusy:false } && songPreviewPane.LayerAnimations.SelectedItem is LayerChoice { Id:-2, Name:"跟随谱面" },"main Layer checkbox");
        ContentsList.SelectedItem = contents.First(c => c.Type == "asset" && package.Assets.Any(a => a.Id == c.Index && a.Kind == "音频"));
        await AwaitState(() => songPreviewPane is null && !working && previewKind == "audio","leave main song preview");
        using (var s3pPane = new SongStudioPane(home,await Request<ExportContext>("export_context"),"gpu",InitializePicker,_ => {})) {
            if (s3pPane.HasPreviewUI) throw new Exception("Export window still contains a player");
            await s3pPane.Initialize(true);
            if (s3pPane.OriginalAudio || s3pPane.StatusText != "就绪") throw new Exception("S3P did not default to full song");
            string output = System.IO.Path.Combine(testRoot,"s3p-" + Guid.NewGuid() + ".wav");
            await s3pPane.Export(output,false);
            using var native = new Windows.Media.Playback.MediaPlayer();
            native.Source = Windows.Media.Core.MediaSource.CreateFromUri(new Uri(output));
            await AwaitState(() => native.PlaybackSession.NaturalDuration.TotalSeconds > 0,"S3P full song with late key");
            if (native.PlaybackSession.NaturalDuration.TotalSeconds < 1.99) throw new Exception("S3P was truncated to first track");
            string movie = System.IO.Path.Combine(testRoot,"s3p-" + Guid.NewGuid() + ".mp4");
            await s3pPane.Export(movie,true);
            if (!File.Exists(movie)) throw new Exception("S3P BGA export: " + s3pPane.StatusText);
        }
        string game = System.IO.Path.Combine(dataRoot,"20_MediaTest","data");
        await Scan(game); FileSearch.Text = "Ascalon";
        await AwaitState(() => FilesList.ItemsSource is List<FileEntry> list && list.Count == 3,"song title search");
        var matches = (List<FileEntry>)FilesList.ItemsSource;
        if (!matches.Any(f => f.Kind == "音频类文件") || !matches.Any(f => f.Kind == "BGA") || !matches.Any(f => f.Relative.Contains("graphic")))
            throw new Exception("曲名未对应音频、BGA 与 Layer");
        FileSearch.Text = "qrispy";
        await AwaitState(() => FilesList.ItemsSource is List<FileEntry> artists && artists.Count == 3,"composer search");
        FileSearch.Text = ""; FilterFiles();
        string boss=System.IO.Path.Combine(dataRoot,"27_BossTest/data/sound/26007.s3p");
        if(File.Exists(boss)) {
            await Open(boss);await AwaitState(()=>package?.Path.EndsWith("26007.s3p")==true && !working,"boss audio");
            using var bossPane=new SongStudioPane(home,await Request<ExportContext>("export_context"),"gpu",InitializePicker,_=>{});
            await bossPane.Initialize(true);bossPane.VideoMode=true;
            await bossPane.Preview(true,false);
            if(bossPane.LayerStates.ItemsSource is not List<LayerState> states || !states.Select(s=>s.Name).SequenceEqual(new[]{"Clear","Failed"}) || bossPane.LayerStates.SelectedItem is not LayerState {Id:"clear"} || bossPane.LayerGauge.Visibility!=Visibility.Visible)
                throw new Exception("Boss states or HP controls missing");
            bossPane.LayerStates.SelectedItem=states.First(s=>s.Id=="failed");bossPane.LayerGauge.Value=50;
            await bossPane.Preview(true,false);
            if(bossPane.StatusText!="就绪")throw new Exception("Boss damage preview: "+bossPane.StatusText);
        }
        await Open(System.IO.Path.Combine(dataRoot,"20_tricoro","data","graphic","test_effect_anime.ifs"));
        await AwaitState(() => animation is not null && !working && !rendering,"MP4 animation selection");
        var animationArgs = new Dictionary<string,object> { ["animation"] = animation!.Id };
        var animationContext = await Request<ExportContext>("export_context",animationArgs);
        using (var worker = new BackgroundCore(home,animationContext)) {
            await worker.Open();
            string target = System.IO.Path.Combine(testRoot,"animation-" + Guid.NewGuid() + ".mp4");
            _ = await worker.Request<ExportReply>("export",new() { ["kind"] = "mp4", ["animation"] = animation.Id,
                ["hidden_layers"] = animationContext.Hidden_layers, ["target"] = target, ["resolution"] = "480p", ["fps"] = 60 });
            using var native = new Windows.Media.Playback.MediaPlayer();
            native.Source = Windows.Media.Core.MediaSource.CreateFromUri(new Uri(target));
            await AwaitState(() => native.PlaybackSession.NaturalDuration.TotalSeconds > 0,"native animation MP4");
            string gif=System.IO.Path.ChangeExtension(target,".gif");
            _ = await worker.Request<ExportReply>("export",new(){["kind"]="gif",["animation"]=animation.Id,["target"]=gif,["resolution"]="480p"});
            if(!File.ReadAllBytes(gif).AsSpan(0,6).SequenceEqual("GIF89a"u8))throw new Exception("Animation GIF export failed");
            await Render(3); if (frame != 3) throw new Exception("Media worker affected interactive renderer");
        }
        await Open(System.IO.Path.Combine(game,"movie","19po.mp4"));
        await AwaitState(() => previewKind == "video" && !working,"media BGA");
        if (!PackageTitle.Text.Contains("Ascalon · Qrispy Joybox") || new EngineInfo("27+",null).Label != "27代+/SDVX6+")
            throw new Exception("Package song/composer label or engine group");
        if (!SongExportMenuItem.IsEnabled || !MP4ExportMenuItem.IsEnabled) throw new Exception("BGA media commands disabled");
        var context = await Request<ExportContext>("export_context");
        using var pane = new SongStudioPane(home,context,"gpu",InitializePicker,worker => mediaJob = worker);
        pane.Player.IsMuted = true;
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, Title = "乐曲验证", Content = pane, CloseButtonText = "关闭" };
        dialog.Resources["ContentDialogMaxWidth"] = 760.0;
        mediaDialogOpen = true;
        var shown = dialog.ShowAsync();
        try {
            SmokeTrace("song studio initialize");
            await pane.Initialize(false);
            if (pane.Title != "Ascalon" || pane.StatusText != "就绪") throw new Exception("Song studio load: " + pane.StatusText);
            if (pane.VideoMode) throw new Exception("Song studio should default to audio");
            pane.BGAPathInput.Text = System.IO.Path.Combine(testRoot,"missing-bga.mp4");
            pane.LayerPathInput.Text = System.IO.Path.Combine(testRoot,"missing-layer.ifs");
            SmokeTrace("independent audio preview"); await pane.Preview();
            await AwaitState(() => pane.Player.PlaybackSession.NaturalDuration.TotalSeconds > 0,"song audio preview");
            if (pane.StatusText != "播放中" || pane.Player.PlaybackSession.NaturalVideoWidth != 0) throw new Exception("Audio preview used visual settings");
            string wav = System.IO.Path.Combine(testRoot,"song-" + Guid.NewGuid() + ".wav");
            await pane.Export(wav,false);
            if (!File.Exists(wav)) throw new Exception("Independent WAV export: " + pane.StatusText);
            pane.VideoMode = true;
            string manualBGA = System.IO.Path.Combine(testRoot,"manual-" + Guid.NewGuid() + ".mp4");
            string manualLayer = System.IO.Path.Combine(testRoot,"manual-" + Guid.NewGuid() + ".ifs");
            File.Copy(System.IO.Path.Combine(game,"movie","19po.mp4"),manualBGA);
            File.Copy(System.IO.Path.Combine(game,"graphic","20032.ifs"),manualLayer);
            pane.BGAPathInput.Text = manualBGA; pane.LayerPathInput.Text = manualLayer;
            SmokeTrace("manual video preview"); await pane.Preview(true);
            if (pane.LayerAnimations.SelectedItem is not LayerChoice { Id: -2, Name: "跟随谱面" } || pane.LayerAnimations.ItemsSource is not IEnumerable<LayerChoice> choices || !choices.Any(a => a.Id >= 0))
                throw new Exception("Unmapped Layer choices: " + pane.LayerAnimations.SelectedItem + ", source=" + pane.LayerAnimations.ItemsSource?.GetType().Name);
            if (pane.StatusText != "播放中") throw new Exception("Manual video preview: " + pane.StatusText);
            await AwaitState(() => pane.Player.PlaybackSession.NaturalDuration.TotalSeconds > 0,"song movie native preview");
            if (pane.StatusText != "播放中") throw new Exception("Song preview: " + pane.StatusText);
            string movie = System.IO.Path.Combine(testRoot,"song-" + Guid.NewGuid() + ".mp4");
            SmokeTrace("song studio MP4 export"); await pane.Export(movie,true);
            if (!File.Exists(movie)) throw new Exception("Song movie export: " + pane.StatusText);
            pane.OutputResolution.SelectedIndex = 2; pane.OutputFPS.SelectedIndex = 1;
            var pending = pane.Preview(true); await Task.Delay(5);
            SmokeTrace("song studio cancel");
            var watch = Stopwatch.StartNew(); pane.Cancel(); await pending; watch.Stop();
            if (pane.IsBusy || watch.Elapsed.TotalSeconds > 2) throw new Exception("Song job cancellation slow");
            SmokeTrace("song studio capture");
            // MediaPlayerElement uses a separate composition surface. Capture its
            // settings, while native playback is verified above through MediaPlayer.
            var image = System.IO.Path.Combine(testRoot,"song-studio.png"); File.WriteAllBytes(image,Array.Empty<byte>()); await CapturePreview(image,pane.SettingsPanel);
            return new() { ["preview_footer_layout"] = true, ["title_search_audio_bga_layer"] = true, ["generic_bga_reverse_lookup"] = true,
                ["native_song_movie_preview"] = true, ["song_mp4_and_wav_export"] = true,
                ["native_animation_mp4_export"] = true, ["interactive_preview_preserved"] = true,
                ["audio_preview_and_export_independent"] = true, ["manual_bga_layer_paths"] = true,
                ["s3p_complete_keysounds_and_bga"] = true,
                ["unmapped_layer_follow_chart"] = true,
                ["complete_song_row_and_visual_checkboxes"] = true, ["export_player_removed"] = true,
                ["song_preview_language_switch_preserves_player"] = true,
                ["cancellation_seconds"] = watch.Elapsed.TotalSeconds, ["screenshot"] = image };
        } finally { SmokeTrace("song studio close"); dialog.Hide(); await shown; mediaDialogOpen = false; }
    }
}
