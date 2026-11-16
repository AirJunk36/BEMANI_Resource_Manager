using System.Diagnostics;
using System.Security.Cryptography;
using Windows.Graphics.Imaging;
using Windows.Storage;

namespace IIDXResourceViewer;

public sealed partial class MainWindow
{
    private static void WriteTestTone(string path)
    {
        const int rate = 22050, frames = rate / 2;
        using var file = new BinaryWriter(File.Create(path));
        file.Write("RIFF"u8); file.Write(36 + frames * 2); file.Write("WAVEfmt "u8);
        file.Write(16); file.Write((short)1); file.Write((short)1); file.Write(rate);
        file.Write(rate * 2); file.Write((short)2); file.Write((short)16);
        file.Write("data"u8); file.Write(frames * 2);
        for (int i = 0; i < frames; i++) file.Write((short)(Math.Sin(i * Math.PI * 2 * 880 / rate) * 10000));
    }

    private static async Task WriteTestImage(string path)
    {
        File.WriteAllBytes(path, Array.Empty<byte>());
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        var pixels = new byte[64 * 64 * 4];
        for (int i = 0; i < pixels.Length; i += 4) { pixels[i] = 70; pixels[i + 1] = 40; pixels[i + 2] = 218; pixels[i + 3] = 255; }
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Straight, 64, 64, 96, 96, pixels);
        await encoder.FlushAsync();
    }

    private async Task<Dictionary<string, object>> RunReplacementSmoke(string dataRoot, string reportRoot)
    {
        var results = new Dictionary<string, object>();
        var fixtures = System.IO.Path.Combine(reportRoot, "replace-fixtures");
        Directory.CreateDirectory(fixtures);
        var png = System.IO.Path.Combine(fixtures, "replacement.png");
        var wav = System.IO.Path.Combine(fixtures, "replacement.wav");
        await WriteTestImage(png); WriteTestTone(wav);
        string source = System.IO.Path.Combine(dataRoot, "20_tricoro", "data", "graphic", "notes00.ifs");
        string before = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source)));
        await Open(source);
        await AwaitState(() => package is not null && !working && PreviewImage.Source is not null, "original image");
        var chosen = package!.Assets.First(a => a.Kind == "图片" && a.Replaceable);
        ContentsList.SelectedItem = contents.First(c => c.Type == "asset" && c.Index == chosen.Id);
        await AwaitState(() => asset?.Name == chosen.Name && !working, "select image replacement");
        if (!ReplaceAssetMenuItem.IsEnabled) throw new Exception("Native replacement menu is disabled");
        await ApplyReplacement(png, false);
        await AwaitState(() => package?.Modified == true && !working && PreviewImage.Source is not null, "replaced image");
        if (!SaveReplacementMenuItem.IsEnabled || !DraftIndicator.Text.Contains("副本")) throw new Exception("Native draft controls did not update");
        string saved = System.IO.Path.Combine(reportRoot, "replacement-copy-" + Guid.NewGuid());
        await Request<ExportReply>("save_replacement", new() { ["target"] = saved });
        if (!File.Exists(System.IO.Path.Combine(saved, "notes00.ifs")) || before != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source))))
            throw new Exception("Replacement save modified the source or omitted the copy");
        UndoReplacement_Click(this, new Microsoft.UI.Xaml.RoutedEventArgs());
        await AwaitState(() => package?.Modified == false && !working && PreviewImage.Source is not null, "undo replacement");
        if (SaveReplacementMenuItem.IsEnabled) throw new Exception("Save replacement stayed enabled after undo");
        results["image_replace_save_undo_source_hash"] = true;

        source = Directory.GetFiles(dataRoot, "*.sd9", SearchOption.AllDirectories).First();
        before = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source)));
        await Open(source);
        await AwaitState(() => asset is not null && previewKind == "audio" && !working, "original sound");
        await ApplyReplacement(wav, false);
        await AwaitState(() => package?.Modified == true && previewKind == "audio" && !working && player.PlaybackSession.NaturalDuration.TotalSeconds is > 0 and < 1, "replaced sound");
        player.Volume = 0; TogglePlay(); await Task.Delay(180); Pause();
        if (player.PlaybackSession.Position.TotalSeconds <= 0 || before != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source))))
            throw new Exception("Replaced audio failed native playback/source protection");
        results["audio_replace_native_seconds"] = player.PlaybackSession.Position.TotalSeconds;

        source = Directory.GetFiles(dataRoot, "*.wmv", SearchOption.AllDirectories).First();
        before = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source)));
        var ffmpegFolder = System.IO.Path.Combine(home, ".venv", "Lib", "site-packages", "imageio_ffmpeg", "binaries");
        var ffmpeg = Directory.GetFiles(ffmpegFolder, "ffmpeg*.exe").First();
        var clip = System.IO.Path.Combine(fixtures, "replacement-bga-" + Guid.NewGuid() + ".mp4");
        var start = new ProcessStartInfo(ffmpeg) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true };
        foreach (string argument in new[] { "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=24", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-n", clip }) start.ArgumentList.Add(argument);
        using (var process = Process.Start(start)!) {
            string error = await process.StandardError.ReadToEndAsync();
            await process.WaitForExitAsync();
            if (process.ExitCode != 0) throw new Exception("Replacement video fixture failed: " + error);
        }
        await Open(source);
        await AwaitState(() => asset is not null && previewKind == "video" && !working, "original BGA");
        await ApplyReplacement(clip, false);
        await AwaitState(() => package?.Modified == true && previewKind == "video" && !working && player.PlaybackSession.NaturalDuration.TotalSeconds is > 0 and < 2, "replaced BGA");
        TogglePlay(); await Task.Delay(250); Pause();
        if (player.PlaybackSession.Position.TotalSeconds <= 0 || before != Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source))))
            throw new Exception("Replaced BGA failed native playback/source protection");
        results["bga_replace_native_seconds"] = player.PlaybackSession.Position.TotalSeconds;
        var screenshot = System.IO.Path.Combine(reportRoot, "replacement-ui.png");
        File.WriteAllBytes(screenshot, Array.Empty<byte>()); await CapturePreview(screenshot);
        return results;
    }
}
