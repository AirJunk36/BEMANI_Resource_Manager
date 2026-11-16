using System.Diagnostics;
using System.Runtime.InteropServices;

namespace BemaniLauncher;

// The application itself uses WinUI; this tiny startup window requires no unpacked DLLs.
internal sealed class StartupWindow : IDisposable
{
    private readonly IntPtr window, label, largeIcon, smallIcon;
    private long lastUpdate;
    private readonly bool hidden = Environment.GetCommandLineArgs().Contains("--exit-after-self-test");
    public StartupWindow()
    {
        if (hidden) return;
        SetProcessDPIAware();
        int x = (GetSystemMetrics(0) - 420) / 2, y = (GetSystemMetrics(1) - 140) / 2;
        window = CreateWindowEx(0x00000001, "STATIC", "BEMANI素材管理器", 0x00c00000 | 0x10000000,
                                x, y, 420, 140, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        label = CreateWindowEx(0, "STATIC", "正在启动…", 0x50000001, 12, 36, 390, 36,
                              window, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        if (Environment.ProcessPath is string executable)
        {
            ExtractIconEx(executable, 0, out largeIcon, out smallIcon, 1);
            SendMessage(window, 0x0080, new IntPtr(1), largeIcon);
            SendMessage(window, 0x0080, IntPtr.Zero, smallIcon);
        }
        SendMessage(label, 0x0030, GetStockObject(17), new IntPtr(1));
        Pump();
    }
    public void Update(string text)
    {
        long now = Stopwatch.GetTimestamp();
        if (now - lastUpdate < Stopwatch.Frequency / 10) return;
        lastUpdate = now;
        if (label != IntPtr.Zero) SetWindowText(label, text);
        Pump();
    }
    public void Pump()
    {
        if (hidden) return;
        while (PeekMessage(out var message, IntPtr.Zero, 0, 0, 1)) { TranslateMessage(ref message); DispatchMessage(ref message); }
    }
    public void Dispose()
    {
        if (window != IntPtr.Zero) DestroyWindow(window);
        if (largeIcon != IntPtr.Zero) DestroyIcon(largeIcon);
        if (smallIcon != IntPtr.Zero) DestroyIcon(smallIcon);
    }
    [DllImport("shell32.dll", EntryPoint = "ExtractIconExW", CharSet = CharSet.Unicode)]
    private static extern uint ExtractIconEx(string path, int index, out IntPtr large, out IntPtr small, uint count);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [StructLayout(LayoutKind.Sequential)]
    private struct Message { public IntPtr Window; public uint Id; public UIntPtr WParam; public IntPtr LParam; public uint Time; public int X, Y; public uint Private; }
    [DllImport("user32.dll", EntryPoint = "CreateWindowExW", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateWindowEx(uint extended, string type, string title, uint style, int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr parameter);
    [DllImport("user32.dll", EntryPoint = "SetWindowTextW", CharSet = CharSet.Unicode)]
    private static extern bool SetWindowText(IntPtr window, string text);
    [DllImport("user32.dll")] private static extern bool DestroyWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("gdi32.dll")] private static extern IntPtr GetStockObject(int index);
    [DllImport("user32.dll", EntryPoint = "SendMessageW")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", EntryPoint = "PeekMessageW")] private static extern bool PeekMessage(out Message message, IntPtr window, uint minimum, uint maximum, uint remove);
    [DllImport("user32.dll")] private static extern bool TranslateMessage(ref Message message);
    [DllImport("user32.dll", EntryPoint = "DispatchMessageW")] private static extern IntPtr DispatchMessage(ref Message message);
}
