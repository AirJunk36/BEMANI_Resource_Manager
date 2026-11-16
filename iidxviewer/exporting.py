"""Exports always target new files; existing files and game paths are refused."""
from pathlib import Path
import re

from PIL import Image


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(". ")
    if not value or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "asset_" + value
    return value[:180]


def check_destination(path, game_root):
    path = Path(path).resolve()
    if game_root and path.is_relative_to(Path(game_root).resolve()):
        raise ValueError("请选择游戏目录外的位置。")
    if path.exists():
        raise FileExistsError("目标已存在，请更名。")
    return path


def write_new(path, data):
    # Exclusive create also prevents an overwrite race after the file dialog.
    with Path(path).open("xb") as f:
        f.write(data)


def original_audio(asset, *, probe=False):
    """Export the encoded media, never the PCM file created for preview."""
    from .resources import AUDIO_EXT, media_suffix, unwrap_media
    if asset.kind != "音频":
        raise ValueError("请选择音频")
    if probe and isinstance(asset.source, Path):
        with asset.source.open("rb") as source:
            head = source.read(32)
            offset = 0
            if head.startswith(b"S3V0"):
                import struct
                if len(head) < 32:
                    raise ValueError("S3V 头无效")
                offset = struct.unpack_from("<I", head, 4)[0]
                if not 16 <= offset < asset.source.stat().st_size:
                    raise ValueError("S3V 头无效")
            source.seek(offset)
            data = source.read(65536)
    else:
        data = unwrap_media(asset.read())
        if probe:
            data = data[:65536]
    suffix = media_suffix(data, Path(asset.name).suffix.lower())
    if suffix not in AUDIO_EXT - {".s3v", ".sd9", ".2dx", ".s3p", ".ssp"}:
        raise ValueError("无法识别原音频格式")
    return safe_name(Path(asset.name).stem) + suffix, data


def export_animation(renderer, target, webp, cancelled=lambda: False, progress=lambda n, t: None):
    length = renderer.animation.length
    fps = renderer.animation.movie.fps
    x0, x1, y0, y1 = getattr(renderer,'rect',renderer.animation.movie.rect)
    if webp and max(1, x1 - x0) * max(1, y1 - y0) * length * 4 > 256 * 1024 * 1024:
        raise ValueError("动画过大，请导出 PNG 序列。")
    frames = []
    target = Path(target)
    if not webp:
        target.mkdir(exist_ok=False)
    for i in range(length):
        if cancelled():
            raise InterruptedError("导出已取消")
        im = renderer.render(i).convertToFormat(__import__("PySide6.QtGui", fromlist=["QImage"]).QImage.Format.Format_RGBA8888)
        pil = Image.frombytes("RGBA", (im.width(), im.height()), bytes(im.constBits()), "raw", "RGBA", im.bytesPerLine())
        if webp:
            frames.append(pil)
        else:
            with (target / f"{i:06}.png").open("xb") as f:
                pil.save(f, "PNG")
        progress(i + 1, length)
    if webp:
        if not frames:
            raise ValueError("动画没有可导出的帧")
        with target.open("xb") as f:
            frames[0].save(f, "WEBP", save_all=True, append_images=frames[1:], duration=[max(1, round((i + 1) * 1000 / fps) - round(i * 1000 / fps)) for i in range(length)], loop=0, lossless=True)
