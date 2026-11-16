using System.Security.Cryptography;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage;
using Windows.Storage.Streams;

namespace IIDXResourceViewer;

// Runs only when explicitly launched with --self-test; ordinary UI has no test flow.
public sealed partial class MainWindow
{
    private static void SmokeTrace(string stage) {
        var folder = Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR");
        if (!string.IsNullOrEmpty(folder)) File.AppendAllText(System.IO.Path.Combine(folder,"native-progress.log"),DateTime.UtcNow.ToString("O") + " " + stage + Environment.NewLine);
    }
    private static Microsoft.UI.Xaml.Controls.CheckBox? LayerCheckBox(DependencyObject node)
    {
        if (node is Microsoft.UI.Xaml.Controls.CheckBox box) return box;
        for (int i = 0; i < Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(node); i++) {
            var found = LayerCheckBox(Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(node, i));
            if (found is not null) return found;
        }
        return null;
    }
    private async Task AwaitState(Func<bool> condition, string step, int seconds = 60)
    {
        SmokeTrace(step);
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (!condition()) {
            if (DateTime.UtcNow > deadline) throw new TimeoutException(step + ": " + Status.Text + " / " + WarningText.Text);
            await Task.Delay(40);
        }
    }

    private async Task CapturePreview(string path, FrameworkElement? element = null)
    {
        var bitmap = new RenderTargetBitmap();
        await bitmap.RenderAsync(element ?? Root);
        var buffer = await bitmap.GetPixelsAsync();
        var bytes = new byte[buffer.Length];
        using (var reader = DataReader.FromBuffer(buffer)) reader.ReadBytes(bytes);
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied, (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, bytes);
        await encoder.FlushAsync();
    }

    private async Task RunSelfTest()
    {
        SmokeTrace("self-test started");
        var testRoot = Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR") ?? Directory.GetParent(home)!.FullName;
        var dataRoot = Environment.GetEnvironmentVariable("IIDX_GAME_ROOT") ?? System.IO.Path.Combine(testRoot, "TestData");
        var reportPath = System.IO.Path.Combine(testRoot, "native-smoke.json");
        var checks = new Dictionary<string, object>();
        checks["architecture"] = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString();
        try {
            if (files.Count != 0 || package is not null || player.Source is not null || working || previewKind != "empty")
                throw new Exception("Default startup loaded resources");
            if (Title != "BEMANI素材管理器") throw new Exception("Unexpected application title: " + Title);
            checks["default_startup_empty"] = true;
            checks["title"] = Title;
            FileSearch.Focus(FocusState.Programmatic);
            if (CanUsePlaybackShortcut()) throw new Exception("Library search intercepted by playback shortcuts");
            AssetSearch.Focus(FocusState.Programmatic);
            if (CanUsePlaybackShortcut()) throw new Exception("Asset search intercepted by playback shortcuts");
            await Scan(dataRoot);
            await AwaitState(() => { FilesList.Focus(FocusState.Programmatic); return CanUsePlaybackShortcut(); }, "playback keyboard focus");
            checks["search_keyboard_editing_preserved"] = true;
            var realFiles = files;
            files = Enumerable.Range(0, 20000).Select(index => {
                string name = index % 5 == 0 ? $"title_{index}.ifs" : $"notes_{index}.ifs";
                return new FileEntry(name, $"33_test/data/graphic/{name}", name, "33_test", "动画/图像资源", 1024);
            }).ToList();
            FilterFiles();
            FileSearch.Text = "not-present"; FileSearch.Text = "TITLE";
            await AwaitState(() => FilesList.ItemsSource is List<FileEntry> list && list.Count == 4000, "latest library search");
            files = realFiles; FileSearch.Text = ""; FilterFiles();
            checks["large_library_search_verified"] = true;
            foreach (var category in new[] { "动画/图像资源", "音频类文件", "BGA" }) {
                Kinds.SelectedItem = category;
                await AwaitState(() => FilesList.ItemsSource is List<FileEntry> result && result.Count > 0 && result.All(f => f.Kind == category), "category search " + category);
                var visible = FilesList.ItemsSource as List<FileEntry> ?? throw new Exception("Filter binding failed");
                if (visible.Count == 0 || visible.Any(f => f.Kind != category)) throw new Exception("Category filter failed: " + category);
                checks[category] = visible.Count;
            }
            Kinds.SelectedIndex = 0;
            string effect = System.IO.Path.Combine(dataRoot, "20_tricoro", "data", "graphic", "test_effect_anime.ifs");
            string hash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(effect)));
            await Open(effect);
            await AwaitState(() => animation is not null && PreviewImage.Source is not null && !rendering, "animation preview");
            if (PreviewImage.Source is not BitmapSource image || image.PixelWidth == 0) throw new Exception("Native image did not decode");
            checks["renderer"] = rendererInfo ?? throw new Exception("Renderer metadata missing");
            if (!rendererInfo.Hardware) throw new Exception("Hardware GPU unavailable: " + rendererInfo.Reason);
            checks["animations"] = package!.Animations.Count;
            await Render(3);
            if (frame != 3) throw new Exception("Frame seeking failed");
            checks["seek_frame"] = frame;
            LayersFlyout.ShowAt(LayersButton);
            await RefreshLayers();
            await AwaitState(() => !layersBusy && layerReport is not null, "animation layer list");
            if (layerRows.Count < 2) throw new Exception("Animation layer list is empty");
            LayerSearch.Text = layerRows[1].Name;
            if (LayersList.ItemsSource is not List<AnimationLayer> matches || matches.Count == 0) throw new Exception("Layer search failed");
            LayerSearch.Text = "";
            LayersList.UpdateLayout();
            await AwaitState(() => LayersList.ContainerFromIndex(0) is not null, "layer checkbox layout");
            var rootCheck = LayerCheckBox(LayersList.ContainerFromIndex(0));
            if (rootCheck is null) throw new Exception("Layer checkbox is missing");
            rootCheck.IsChecked = false;
            await AwaitState(() => !layersBusy && !rendering && layerRows.All(r => !r.Visible), "layer checkbox hide");
            if (layerRows.Any(r => r.Visible)) throw new Exception("Hide all layers failed");
            if (PreviewImage.Source is not WriteableBitmap hiddenImage) throw new Exception("Hidden preview is missing");
            using (var pixels = hiddenImage.PixelBuffer.AsStream()) {
                var bytes = new byte[checked(hiddenImage.PixelWidth * hiddenImage.PixelHeight * 4)];
                pixels.ReadExactly(bytes);
                for (int i = 3; i < bytes.Length; i += 4)
                    if (bytes[i] != 0) throw new Exception("Hidden material remained in the native preview");
            }
            rootCheck.IsChecked = true;
            await AwaitState(() => !layersBusy && !rendering && layerRows.All(r => r.Visible), "layer checkbox restore");
            if (layerRows.Any(r => !r.Visible)) throw new Exception("Restore layers failed");
            LayersFlyout.Hide();
            checks["animation_layer_controls"] = true;
            var animationCategory = ((List<ContentItem>)ContentsList.ItemsSource).First(c => c.IsHeader && c.Group == "动画");
            var beforeFold = animation;
            ContentsList.SelectedItem = animationCategory;
            if (((List<ContentItem>)ContentsList.ItemsSource).Any(c => c.Type == "animation") || animation != beforeFold)
                throw new Exception("Category did not collapse while preserving preview");
            AssetSearch.Text = beforeFold!.Name;
            await AwaitState(() => ((List<ContentItem>)ContentsList.ItemsSource).Any(c => c.Type == "animation"), "search in collapsed category");
            AssetSearch.Text = "";
            await AwaitState(() => ((List<ContentItem>)ContentsList.ItemsSource).Any(c => c.IsHeader), "category headers after search");
            ContentsList.SelectedItem = ((List<ContentItem>)ContentsList.ItemsSource).First(c => c.IsHeader && c.Group == "动画");
            if (!((List<ContentItem>)ContentsList.ItemsSource).Any(c => c.Type == "animation")) throw new Exception("Category did not expand");
            FoldAll_Click(new Microsoft.UI.Xaml.Controls.Button { Tag = "collapse" }, new RoutedEventArgs());
            if (((List<AnimationLayer>)LayersList.ItemsSource).Count != 1) throw new Exception("Material groups did not collapse");
            FoldAll_Click(new Microsoft.UI.Xaml.Controls.Button { Tag = "expand" }, new RoutedEventArgs());
            if (((List<AnimationLayer>)LayersList.ItemsSource).Count != layerRows.Count) throw new Exception("Material groups did not expand");
            FoldLayer_Click(new Microsoft.UI.Xaml.Controls.Button { Tag = "root" }, new RoutedEventArgs());
            LayerSearch.Text = layerRows[1].Name;
            if (((List<AnimationLayer>)LayersList.ItemsSource).Count == 0) throw new Exception("Collapsed material search failed");
            LayerSearch.Text = "";
            if (PreviewImage.Source is not WriteableBitmap restoredImage) throw new Exception("Restored preview missing");
            int pixel = -1;
            using (var pixels = restoredImage.PixelBuffer.AsStream()) {
                var bytes = new byte[checked(restoredImage.PixelWidth * restoredImage.PixelHeight * 4)]; pixels.ReadExactly(bytes);
                for (int i = 3; i < bytes.Length; i += 4) if (bytes[i] > 128) { pixel = i / 4; break; }
            }
            if (pixel < 0) throw new Exception("No visible pixel for picking");
            double u = (pixel % restoredImage.PixelWidth + .5) / restoredImage.PixelWidth;
            double v = (pixel / restoredImage.PixelWidth + .5) / restoredImage.PixelHeight;
            TogglePlay(); await PickLayer(u, v);
            await AwaitState(() => !layersBusy && !pickingLayer, "preview material pick");
            if (playing || selectedLayerID is null || selectedLayerBounds is not { Length: 4 } ||
                LayersList.SelectedItem is not AnimationLayer picked || picked.Id != selectedLayerID ||
                SelectionOutline.Visibility != Visibility.Visible || collapsedLayers.Count != 0)
                throw new Exception("Preview pick did not pause, expand and select material");
            await PickLayer(-1, -1);
            if (selectedLayerID is not null || LayersList.SelectedItem is not null || SelectionOutline.Visibility != Visibility.Collapsed)
                throw new Exception("Empty preview did not deselect material");
            checks["preview_material_picking"] = true;
            checks["category_and_material_folding"] = true;
            await Task.WhenAll(Render(1), Render(4), Render(8));
            await AwaitState(() => frame == 8 && !rendering, "latest rapid seek");
            checks["rapid_seek_latest_frame"] = true;
            if (timer.IsRunning) throw new Exception("Paused animation kept the playback timer running");
            checks["idle_playback_timer_stopped"] = true;
            var rateCheck = package.Animations.First(a => Math.Abs(a.Fps - 30) < .01 && a.Length > 8);
            ContentsList.SelectedItem = contents.First(c => c.Type == "animation" && c.Index == rateCheck.Id);
            await AwaitState(() => animation?.Id == rateCheck.Id && !rendering && !working, "60 FPS source selection");
            TogglePlay();
            if (PlayIcon.Symbol != Microsoft.UI.Xaml.Controls.Symbol.Pause) throw new Exception("Pause control did not use a native icon");
            baseFrame = 0; startedAt = Now - .101;
            Timer_Tick(timer, new object());
            Pause();
            if (PlayIcon.Symbol != Microsoft.UI.Xaml.Controls.Symbol.Play) throw new Exception("Play control did not restore the native icon");
            await AwaitState(() => !rendering, "60 FPS playback settle");
            if (frame < 6 || frame > 7 || FpsChoice.SelectedItem as string != "60 FPS") throw new Exception("Animation clock did not use 60 FPS: " + frame);
            checks["default_animation_fps"] = AnimationPlaybackFps;
            checks["frames_at_0_1_seconds"] = frame;
            FpsChoice.SelectedIndex = 1;
            TogglePlay(); baseFrame = 0; startedAt = Now - .051;
            Timer_Tick(timer, new object()); Pause();
            await AwaitState(() => !rendering, "120 FPS playback settle");
            if (frame < 6 || frame > 7) throw new Exception("Animation clock did not use 120 FPS: " + frame);
            checks["frames_at_120_fps_0_05_seconds"] = frame;
            FpsChoice.SelectedIndex = 0;
            RendererChoice.SelectedIndex = 0;
            await AwaitState(() => !rendering && rendererInfo?.Api == "CPU", "CPU renderer selection");
            ResolutionChoice.SelectedIndex = 0;
            await AwaitState(() => !rendering, "720p selection");
            RendererChoice.SelectedIndex = 1;
            await AwaitState(() => !rendering && rendererInfo?.Hardware == true, "GPU renderer selection");
            ResolutionChoice.SelectedIndex = 1;
            await AwaitState(() => !rendering, "1080p selection");
            checks["preview_settings_switching"] = true;
            foreach (int resolution in new[] { 2, 3, 4, 1 }) {
                ResolutionChoice.SelectedIndex = resolution;
                await AwaitState(() => !rendering, "portrait/480p selection");
            }
            checks["portrait_and_480p_selection"] = true;
            for (int mode = 0; mode < 5; mode++) {
                ScaleChoice.SelectedIndex = mode;
                if (PreviewImage.Source is not BitmapSource current) throw new Exception("Display mode lost its image");
                double ratio = PreviewImage.Width / PreviewImage.Height;
                if (mode == 0 && (PreviewImage.Width != current.PixelWidth || PreviewImage.Height != current.PixelHeight)
                    || mode == 1 && Math.Abs(ratio - 16.0 / 9) > .001
                    || mode == 2 && Math.Abs(ratio - 4.0 / 3) > .001
                    || mode == 3 && Math.Abs(ratio - current.PixelWidth / (double)current.PixelHeight) > .001
                    || mode == 4 && (Math.Abs(PreviewImage.Width - PreviewGrid.ActualWidth) > 1 || Math.Abs(PreviewImage.Height - PreviewGrid.ActualHeight) > 1))
                    throw new Exception("Display mode did not apply: " + ScaleChoice.SelectedItem);
            }
            ScaleChoice.SelectedIndex = 3;
            checks["display_modes_verified"] = true;
            TogglePlay();
            var seen = new HashSet<int> { frame };
            for (int i = 0; i < 12; i++) { await Task.Delay(25); seen.Add(frame); }
            Pause();
            if (seen.Count < 2) throw new Exception("Animation playback did not advance");
            checks["playback_distinct_frames"] = seen.Count;
            await AwaitState(() => !rendering, "playback settle");
            string screenshot = System.IO.Path.Combine(testRoot, "native-smoke.png");
            File.WriteAllBytes(screenshot, Array.Empty<byte>());
            await CapturePreview(screenshot);
            checks["screenshot"] = screenshot;
            string export = System.IO.Path.Combine(testRoot, "frame-" + Guid.NewGuid() + ".png");
            await Request<ExportReply>("export", new() { ["kind"] = "frame", ["animation"] = animation!.Id, ["frame"] = 3, ["target"] = export });
            if (!File.Exists(export) || hash != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(effect)))) throw new Exception("Export/source protection failed");
            checks["export_and_source_hash"] = true;
            player.Volume = 0;
            string audioFile = Directory.GetFiles(dataRoot, "*.sd9", SearchOption.AllDirectories).First();
            await Open(audioFile);
            await AwaitState(() => previewKind == "audio" && player.PlaybackSession.NaturalDuration.TotalSeconds > 0, "native audio");
            TogglePlay(); await Task.Delay(300); Pause();
            if (player.PlaybackSession.Position.TotalSeconds <= 0) throw new Exception("Audio playback did not advance");
            checks["native_audio_seconds"] = player.PlaybackSession.NaturalDuration.TotalSeconds;
            byte[] sd9 = File.ReadAllBytes(audioFile);
            string sspSource = System.IO.Path.Combine(testRoot, "ssp-source-" + Guid.NewGuid());
            Directory.CreateDirectory(sspSource);
            string ssp = System.IO.Path.Combine(sspSource, "sys_sound.ssp");
            using (var bank = new MemoryStream()) {
                using var writer = new BinaryWriter(bank);
                int header = 0x48 + 81 * 4;
                writer.Write(new byte[header]); bank.Position = 0;
                writer.Write(System.Text.Encoding.ASCII.GetBytes("sys_sound.ssp"));
                bank.Position = 0x10; writer.Write(header); writer.Write(2);
                for (int i = 0; i < 2; i++) {
                    bank.Position = 0x48 + i * 80 * 4; writer.Write(header + i * sd9.Length);
                }
                bank.Position = header; writer.Write(sd9); writer.Write(sd9);
                File.WriteAllBytes(ssp, bank.ToArray());
            }
            string sspHash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(ssp)));
            await Open(ssp);
            await AwaitState(() => !working && previewKind == "audio" && player.PlaybackSession.NaturalDuration.TotalSeconds > 0, "SSP native audio");
            if (package!.Assets.Count != 2 || !package.Assets.Any(a => a.Name == "sys_sound_00080.wav")) throw new Exception("SSP sparse IDs lost");
            TogglePlay(); await Task.Delay(200); Pause();
            if (player.PlaybackSession.Position.TotalSeconds <= 0) throw new Exception("SSP playback did not advance");
            string unpacked = System.IO.Path.Combine(testRoot, "ssp-" + Guid.NewGuid());
            await Request<ExportReply>("export", new() { ["kind"] = "audio", ["target"] = unpacked });
            if (Directory.GetFiles(unpacked, "*.wav").Length != 2 || !File.Exists(System.IO.Path.Combine(unpacked, "index.json")) ||
                sspHash != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(ssp)))) throw new Exception("SSP export/source protection failed");
            checks["ssp_native_preview_and_unpack"] = true;
            string s3p = Directory.GetFiles(dataRoot, "*.s3p", SearchOption.AllDirectories)
                .First(path => !File.Exists(System.IO.Path.ChangeExtension(path,".1")));
            await Open(s3p);
            await AwaitState(() => !working && previewKind == "audio", "S3P audio");
            string audioSource = System.IO.Path.Combine(testRoot, "audio-source-" + Guid.NewGuid());
            Directory.CreateDirectory(audioSource);
            string s3v = System.IO.Path.Combine(audioSource, "voice.s3v");
            await Request<ExportReply>("export", new() { ["kind"] = "asset", ["asset"] = asset!.Id, ["target"] = s3v });
            await Open(s3v);
            await AwaitState(() => !working && previewKind == "audio", "S3V audio");
            var originalInfo = await Request<AudioExportInfo>("audio_export_info", new() { ["asset"] = asset!.Id });
            if (originalInfo.Name != "voice.wma" || !SongExportMenuItem.IsEnabled) throw new Exception("Original audio export unavailable");
            string wma = System.IO.Path.Combine(testRoot, "original-" + Guid.NewGuid() + ".wma");
            using (var audioPane = new SongStudioPane(home,await Request<ExportContext>("export_context"),RenderMode,InitializePicker,worker => mediaJob = worker,backend,asset)) {
                await audioPane.Initialize(false);
                if (!audioPane.OriginalAudio) throw new Exception("S3V did not open in original audio mode");
                await audioPane.Preview(); await audioPane.Export(wma,false);
            }
            if (!File.ReadAllBytes(wma).SequenceEqual(File.ReadAllBytes(s3v))) throw new Exception("Original audio was transcoded");
            checks["original_audio_export"] = true;
            string bgaFile = Directory.GetFiles(dataRoot, "*.wmv", SearchOption.AllDirectories).First();
            await Open(bgaFile);
            await AwaitState(() => previewKind == "video" && player.PlaybackSession.NaturalDuration.TotalSeconds > 0, "native BGA");
            ScaleChoice.SelectedIndex = 4;
            if (!double.IsFinite(Video.Width) || !double.IsFinite(Video.Height)
                || Math.Abs(Video.Width - PreviewGrid.ActualWidth) > 1 || Math.Abs(Video.Height - PreviewGrid.ActualHeight) > 1)
                throw new Exception("BGA stretch did not apply");
            ScaleChoice.SelectedIndex = 3;
            checks["bga_display_modes_verified"] = true;
            TogglePlay(); await Task.Delay(400); Pause();
            if (player.PlaybackSession.Position.TotalSeconds <= 0) throw new Exception("BGA playback did not advance");
            checks["native_bga_seconds"] = player.PlaybackSession.NaturalDuration.TotalSeconds;
            var legacyChecks = new Dictionary<string, int>();
            foreach (var relative in new[] {
                "14_GOLD/data/graph/sys/0200/owl.bin",
                "16_EMPRESS/data/graph/sys/0200/osprey.bin",
                "18_Resort_Anthem/data/graph_data/sys/0200.bin",
                "19_Lincle_cn/contents/modules/data/graphic/0200.bin"
            }) {
                await Open(System.IO.Path.Combine(dataRoot, relative.Replace('/', System.IO.Path.DirectorySeparatorChar)));
                await AwaitState(() => animation is not null && PreviewImage.Source is not null && !rendering, "legacy BIN " + relative);
                if (PreviewImage.Source is not BitmapSource legacyImage || legacyImage.PixelWidth == 0) throw new Exception("Legacy native image failed");
                await Render(3);
                if (frame != 3) throw new Exception("Legacy BIN seek failed");
                TogglePlay();
                await AwaitState(() => frame != 3, "legacy playback frame advance", 10);
                Pause();
                await AwaitState(() => !rendering, "legacy playback settle");
                legacyChecks[relative] = package!.Animations.Count;
            }
            checks["legacy_bin_native_previews"] = legacyChecks;
            string legacyScreenshot = System.IO.Path.Combine(testRoot, "legacy-bin.png");
            File.WriteAllBytes(legacyScreenshot, Array.Empty<byte>());
            await CapturePreview(legacyScreenshot);
            checks["replacements"] = await RunReplacementSmoke(dataRoot, testRoot);
            player.IsMuted = true;
            foreach (string bank in new[] { "0101/01011.2dx", "0110/0110.2dx", "0109/0109.2dx", "0100/01001.2dx" }) {
                string path = System.IO.Path.Combine(dataRoot, "12_HAPPY_SKY/data/sd_data", bank);
                string before = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)));
                await Open(path);
                await AwaitState(() => !working && previewKind == "audio" && player.PlaybackSession.NaturalDuration.TotalSeconds > 0, "legacy audio " + bank);
                TogglePlay();
                await AwaitState(() => player.PlaybackSession.Position.TotalSeconds > 0, "legacy audio playback " + bank);
                Pause();
                if (before != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)))) throw new Exception("Legacy audio source changed");
            }
            checks["legacy_encrypted_audio_native_playback"] = true;
            checks["native_play_pause_icons"] = true;
            string inspectSource = System.IO.Path.Combine(dataRoot, "33_Sparkle_Shower", "data", "graphic", "0", "title.ifs");
            if (File.Exists(inspectSource) && File.Exists(System.IO.Path.Combine(dataRoot, "33_Sparkle_Shower", "afp-core.dll"))) {
                string inspectedHash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(inspectSource)));
                await Open(inspectSource);
                await AwaitState(() => animation is not null && !rendering && !working, "animation inspection selection");
                if (!AnimationInfoButton.IsEnabled || animation?.Name != "title") throw new Exception("Main animation entry was not selected");
                var inspected = await Request<JsonElement>("inspect_animation", new() { ["animation"] = animation!.Id });
                var details = inspected.GetProperty("report");
                if (!details.GetProperty("dll").GetProperty("ok").GetBoolean()
                    || details.GetProperty("game_usage").GetProperty("purpose").GetString() != "标题画面"
                    || details.GetProperty("layers").GetArrayLength() == 0
                    || inspectedHash != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(inspectSource))))
                    throw new Exception("DLL animation inspection failed");
                checks["dll_animation_inspection"] = true;
                await Render(3);
                if (frame != 3) throw new Exception("Seeking after inspection failed");
                var repeated = await Request<JsonElement>("inspect_animation", new() { ["animation"] = animation!.Id });
                if (!repeated.GetProperty("report").GetProperty("dll").GetProperty("ok").GetBoolean())
                    throw new Exception("Repeated inspection failed");
                await Open(effect);
                await AwaitState(() => animation is not null && !rendering && !working, "resource switch after inspection");
                await Render(5);
                if (frame != 5) throw new Exception("Render after inspection/resource switch failed");
                checks["inspection_backend_continuity"] = true;
            }
            checks["media"] = await RunMediaSmoke(dataRoot,testRoot);
            if(File.Exists(System.IO.Path.Combine(dataRoot,"live2d","rasis_ver7","rasis_ver7.model3.json")))checks["live2d"]=await RunLiveSmoke(dataRoot,testRoot);
            string previousLanguage=I18n.Code;var languagePackage=package;int languageFrame=frame;
            ready=false;LanguageChoice.SelectedIndex=1;ready=true;
            I18n.SetLanguage("en",false);ApplyLanguage();
            if(Title!="BEMANI Resource Manager"||FileSearch.PlaceholderText!="Search files, paths, songs or composers"||I18n.Tr("副本 · 3 次修改")!="Copy · 3 changes")throw new Exception("English interface translation failed");
            if(I18n.Tr("原音轨")!="Original track"||I18n.Tr("正常")!="Normal")throw new Exception("English media/editor translation failed");
            if(new ContentItem("正常","","animation",0).DisplayName!="正常")throw new Exception("Localization changed an animation name");
            Root.UpdateLayout();
            if(AppHeading.ActualHeight>48||PreviewGrid.ActualHeight<60)throw new Exception("English heading compressed the preview");
            checks["english_preview_height"]=PreviewGrid.ActualHeight;
            foreach(var panel in new[]{PreviewTools,PreviewSettings})foreach(var child in panel.Children.OfType<FrameworkElement>()) {
                if(child.Visibility!=Visibility.Visible)continue;
                var bounds=child.TransformToVisual(panel).TransformBounds(new Windows.Foundation.Rect(0,0,child.ActualWidth,child.ActualHeight));
                if(bounds.Right>panel.ActualWidth+1||bounds.Bottom>panel.ActualHeight+1)throw new Exception("English preview toolbar clipped: "+child.Name);
            }
            checks["english_toolbar_fits"]=true;
            string englishScreenshot=Path.Combine(testRoot,"interface-en.png");File.WriteAllBytes(englishScreenshot,Array.Empty<byte>());await CapturePreview(englishScreenshot);
            ready=false;LanguageChoice.SelectedIndex=previousLanguage=="en"?1:0;ready=true;
            I18n.SetLanguage(previousLanguage,false);ApplyLanguage();
            if(Title!="BEMANI素材管理器"||FileSearch.PlaceholderText!="搜索文件、路径或乐曲名")throw new Exception("Chinese interface restore failed");
            if(!ReferenceEquals(package,languagePackage)||frame!=languageFrame)throw new Exception("Language switch reloaded the asset");
            checks["english_and_chinese_interface"]=true;
            checks["ok"] = true;
            Status.Text = "测试通过";
        } catch (Exception ex) {
            checks["ok"] = false; checks["error"] = ex.ToString(); checks["status"] = Status.Text;
            ShowError(ex);
        } finally {
            File.WriteAllText(reportPath, JsonSerializer.Serialize(checks, new JsonSerializerOptions { WriteIndented = true }));
            if (Environment.GetCommandLineArgs().Contains("--exit-after-self-test")) Close();
        }
    }
}
