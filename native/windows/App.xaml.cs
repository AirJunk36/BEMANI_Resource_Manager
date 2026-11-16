using Microsoft.UI.Xaml;

namespace IIDXResourceViewer;

public partial class App : Application
{
    private MainWindow? window;
    public App()
    {
        InitializeComponent();
        UnhandledException += (_, error) => {
            var report = Environment.GetEnvironmentVariable("IIDX_TEST_REPORT_DIR");
            if (!string.IsNullOrEmpty(report)) {
                Directory.CreateDirectory(report);
                File.AppendAllText(System.IO.Path.Combine(report, "native-error.log"), error.Exception + Environment.NewLine);
            }
        };
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new MainWindow();
        window.Activate();
        var readyName = Environment.GetEnvironmentVariable("BEMANI_LAUNCH_READY");
        if (!string.IsNullOrEmpty(readyName)) {
            try { using var ready = EventWaitHandle.OpenExisting(readyName); ready.Set(); }
            catch (WaitHandleCannotBeOpenedException) { }
        }
    }
}
