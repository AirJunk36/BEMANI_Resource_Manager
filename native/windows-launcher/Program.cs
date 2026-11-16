using System.Diagnostics;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace BemaniLauncher;

internal static class Program
{
    private const string Title = "BEMANI素材管理器";
    private static readonly byte[] Magic = "BEMANI_BUNDLE_V1"u8.ToArray();
    private const int FooterSize = 16 + 8 + 32;

    private static void Trace(string message)
    {
        var folder = Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR");
        if (!string.IsNullOrEmpty(folder)) { Directory.CreateDirectory(folder); File.AppendAllText(Path.Combine(folder, "launcher.log"), DateTime.UtcNow.ToString("O") + " " + message + Environment.NewLine); }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try {
            Trace("Launcher started");
            using var file = File.OpenRead(Environment.ProcessPath!);
            if (file.Length < FooterSize) throw new InvalidDataException("应用文件不完整，请重新下载。");
            file.Position = file.Length - FooterSize;
            var footer = new byte[FooterSize]; file.ReadExactly(footer);
            long size = BitConverter.ToInt64(footer, 16);
            if (!footer.AsSpan(0, 16).SequenceEqual(Magic) || size <= 0 || size > file.Length - FooterSize)
                throw new InvalidDataException("应用文件不完整，请重新下载。");
            var hash = footer.AsSpan(24, 32).ToArray();
            string id = Convert.ToHexString(hash).ToLowerInvariant();
            string cacheRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BEMANIResourceManager", "runtime");
            Directory.CreateDirectory(cacheRoot);
            string home = Path.Combine(cacheRoot, id);
            Trace("Runtime: " + home);
            using var mutex = new Mutex(false, "Local\\BEMANI.Materials." + id);
            using (var splash = new StartupWindow()) {
                while (true) {
                    try { if (mutex.WaitOne(50)) break; }
                    catch (AbandonedMutexException) { break; }
                    splash.Update("正在启动，请稍候…");
                }
                try {
                    string marker = Path.Combine(home, ".ready");
                    if (!File.Exists(marker) || File.ReadAllText(marker) != id || !File.Exists(Path.Combine(home, "IIDXResourceViewer.exe"))
                            || !File.Exists(Path.Combine(home, ".venv", "Scripts", "python.exe")) || !File.Exists(Path.Combine(home, "backend.py"))) {
                        using var payload = new PayloadStream(file, file.Length - FooterSize - size, size);
                        Trace("Preparing payload");
                        splash.Update("正在准备首次启动…");
                        // Verify the complete payload before writing or running any executable.
                        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
                        var buffer = new byte[1024 * 1024];
                        int read;
                        while ((read = payload.Read(buffer)) != 0) { digest.AppendData(buffer, 0, read); splash.Pump(); }
                        if (!CryptographicOperations.FixedTimeEquals(digest.GetHashAndReset(), hash))
                            throw new InvalidDataException("应用文件校验失败，请重新下载。");
                        payload.Position = 0;
                        string staging = Path.Combine(cacheRoot, id + ".new-" + Guid.NewGuid().ToString("N"));
                        Directory.CreateDirectory(staging);
                        try {
                            Extract(payload, staging, splash);
                            File.WriteAllText(Path.Combine(staging, ".ready"), id);
                            if (Directory.Exists(home)) Directory.Delete(home, true);
                            Directory.Move(staging, home);
                            Trace("Payload prepared");
                        } finally { if (Directory.Exists(staging)) Directory.Delete(staging, true); }
                    }
                } finally { mutex.ReleaseMutex(); }
            }
            var start = new ProcessStartInfo(Path.Combine(home, "IIDXResourceViewer.exe")) {
                UseShellExecute = false, WorkingDirectory = home
            };
            start.Environment["IIDX_VIEWER_HOME"] = home;
            foreach (var arg in args) start.ArgumentList.Add(arg);
            string readyName = "Local\\BEMANI.Launch." + Guid.NewGuid().ToString("N");
            using var ready = new EventWaitHandle(false, EventResetMode.ManualReset, readyName);
            start.Environment["BEMANI_LAUNCH_READY"] = readyName;
            for (int attempt = 0; ; attempt++) {
                using var app = Process.Start(start) ?? throw new IOException("应用未能启动，请重试。");
                Trace("App started: " + app.Id);
                app.WaitForExit();
                Trace("App exit: " + app.ExitCode + "; window ready: " + ready.WaitOne(0));
                if (ready.WaitOne(0) || app.ExitCode == 0) return app.ExitCode;
                // Windows ARM can abort native runtime initialization before any
                // application window exists. Never restart a session after it opens.
                if (attempt >= 2 || app.ExitCode is not (unchecked((int)0xc0000374) or unchecked((int)0xc000027b)))
                    throw new IOException($"应用启动失败（{app.ExitCode:X8}），请重试。");
                Trace("Retrying native runtime initialization");
                Thread.Sleep(250);
            }
        } catch (Exception ex) {
            if (args.Contains("--exit-after-self-test")) {
                var report = Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR");
                if (!string.IsNullOrEmpty(report)) { Directory.CreateDirectory(report); File.WriteAllText(Path.Combine(report, "launcher-error.log"), ex.ToString()); }
            } else MessageBox(IntPtr.Zero, ex.Message, Title, 0x10);
            return 1;
        }
    }

    private static void Extract(Stream payload, string staging, StartupWindow splash)
    {
        using var archive = new ZipArchive(payload, ZipArchiveMode.Read, true, Encoding.UTF8);
        string prefix = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;
        long total = archive.Entries.Sum(e => e.Length), done = 0;
        var buffer = new byte[1024 * 1024];
        foreach (var entry in archive.Entries) {
            string name = entry.FullName.Replace('\\', '/');
            string target = Path.GetFullPath(Path.Combine(staging, name));
            if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || name.Contains(':')
                    || ((entry.ExternalAttributes >> 16) & 0xf000) == 0xa000)
                throw new InvalidDataException("应用包包含无效路径。");
            if (name.EndsWith('/')) { Directory.CreateDirectory(target); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var incoming = entry.Open();
            using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            int read;
            while ((read = incoming.Read(buffer)) != 0) {
                output.Write(buffer, 0, read); done += read;
                splash.Update($"正在准备首次启动… {Math.Min(100, done * 100 / Math.Max(1, total))}%");
            }
        }
        if (!File.Exists(Path.Combine(staging, "IIDXResourceViewer.exe")) || !File.Exists(Path.Combine(staging, "backend.py")))
            throw new InvalidDataException("应用包缺少必要文件。");
    }

    [DllImport("user32.dll", EntryPoint = "MessageBoxW", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr window, string text, string caption, uint type);
}

// ZipArchive sees only the appended ZIP, with offsets relative to its start.
internal sealed class PayloadStream(Stream file, long offset, long length) : Stream
{
    private long position;
    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => length;
    public override long Position { get => position; set { if (value < 0 || value > length) throw new IOException("无效包偏移"); position = value; } }
    public override int Read(byte[] buffer, int start, int count) => Read(buffer.AsSpan(start, count));
    public override int Read(Span<byte> buffer)
    {
        file.Position = offset + position;
        int read = file.Read(buffer[..(int)Math.Min(buffer.Length, length - position)]);
        position += read; return read;
    }
    public override long Seek(long value, SeekOrigin origin) => Position = origin switch {
        SeekOrigin.Begin => value, SeekOrigin.Current => position + value, SeekOrigin.End => length + value,
        _ => throw new ArgumentOutOfRangeException(nameof(origin))
    };
    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int start, int count) => throw new NotSupportedException();
}
