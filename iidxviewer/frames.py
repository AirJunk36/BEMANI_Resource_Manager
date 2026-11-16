"""A bounded shared frame ring: native previews avoid PNG compression and decode."""
import mmap
from pathlib import Path

from PySide6.QtGui import QImage


class SharedFrames:
    MAX_PIXELS = 1920 * 1080
    SLOT_BYTES = MAX_PIXELS * 4
    SLOTS = 3

    def __init__(self, cache):
        self.path = Path(cache) / "preview.frames"
        self.file = self.path.open("w+b")
        self.file.truncate(self.SLOT_BYTES * self.SLOTS)
        self.memory = mmap.mmap(self.file.fileno(), 0)
        self.serial = 0

    def write(self, image):
        if image.width() * image.height() > self.MAX_PIXELS:
            raise ValueError("预览帧超出共享缓存大小")
        if image.format() != QImage.Format.Format_ARGB32_Premultiplied:
            image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.serial += 1
        offset = (self.serial % self.SLOTS) * self.SLOT_BYTES
        size = image.sizeInBytes()
        self.memory[offset:offset + size] = image.constBits()
        return {"kind": "raw", "path": str(self.path), "offset": offset, "bytes": size,
                "width": image.width(), "height": image.height(), "stride": image.bytesPerLine(),
                "pixel_format": "BGRA8_PREMULTIPLIED", "serial": self.serial}

    def close(self):
        self.memory.close(); self.file.close()
        self.path.unlink(missing_ok=True)
