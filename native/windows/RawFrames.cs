using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml.Media.Imaging;

namespace IIDXResourceViewer;

internal sealed class RawFrameReader : IDisposable
{
    private string? path;
    private MemoryMappedFile? file;
    private MemoryMappedViewAccessor? view;
    private byte[] bytes = Array.Empty<byte>();
    private WriteableBitmap? bitmap;

    public WriteableBitmap Read(PreviewReply frame)
    {
        if (frame.Pixel_format != "BGRA8_PREMULTIPLIED" || frame.Path is null || frame.Width is not > 0 or > 1920
                || frame.Height is not > 0 or > 1920 || frame.Width * frame.Height > 1920 * 1080
                || frame.Stride != frame.Width * 4 || frame.Offset is not >= 0)
            throw new IOException("预览帧格式无效");
        int width = frame.Width.Value, height = frame.Height.Value, stride = frame.Stride!.Value;
        if (path != frame.Path || view is null) {
            view?.Dispose(); file?.Dispose();
            var stream = new FileStream(frame.Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            try { file = MemoryMappedFile.CreateFromFile(stream, null, 0, MemoryMappedFileAccess.Read, HandleInheritability.None, false); }
            catch { stream.Dispose(); throw; }
            view = file.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read); path = frame.Path;
        }
        int count = checked(stride * height);
        if (frame.Offset > view.Capacity - count) throw new IOException("预览帧超出缓存范围");
        if (bytes.Length != count) bytes = new byte[count];
        view.ReadArray(frame.Offset.Value, bytes, 0, count);
        if (bitmap is null || bitmap.PixelWidth != width || bitmap.PixelHeight != height) bitmap = new WriteableBitmap(width, height);
        using (var destination = bitmap.PixelBuffer.AsStream()) destination.Write(bytes, 0, count);
        bitmap.Invalidate();
        return bitmap;
    }

    public void Dispose() { view?.Dispose(); file?.Dispose(); view = null; file = null; }
}
