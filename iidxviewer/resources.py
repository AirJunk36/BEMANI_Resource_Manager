"""Read IFS objects lazily; game files are never extracted in place or modified."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import re
import struct

from ifstools.ifs import IFS
from ifstools.handlers.image_file import ImageFile
from ifstools.handlers.tex_folder import ImageCanvas
from PIL import Image

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif", ".avif"}
VIDEO_EXT = {".mp4", ".wmv", ".mpg", ".mpeg", ".avi", ".webm", ".m4v", ".mov", ".mkv", ".4"}
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".flac", ".wma", ".s3v", ".2dx", ".sd9", ".s3p", ".ssp", ".m4a", ".aac", ".aif", ".aiff", ".opus"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT
RESOURCE_EXT = IMAGE_EXT | {".ifs", ".bin", ".idx", ".idr", ".gcz", ".gcr", ".moc3"}
SUPPORTED_EXT = RESOURCE_EXT | MEDIA_EXT


def catalog_kind(path: Path):
    """Library categories describe resource files, independently of IFS contents."""
    ext = path.suffix.lower()
    if ext in RESOURCE_EXT or path.name.lower().endswith('.model3.json'):
        return "动画/图像资源"
    if ext in VIDEO_EXT:
        return "BGA"
    if ext == ".s3v":
        # S3V/ASF is used for both sound and BGA. Prefer stream metadata when
        # available, and the movie directory when a file cannot be read.
        try:
            with path.open("rb") as file:
                header = file.read(65536)
            if b"\xc0\xef\x19\xbc\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b" in header:
                return "BGA"
            if b"\x40\x9e\x69\xf8\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b" in header:
                return "音频类文件"
        except OSError:
            pass
        if any(p.lower() in {"movie", "movies", "bga", "video"} for p in path.parts[:-1]):
            return "BGA"
    return "音频类文件"


def natural_key(value):
    return tuple((0, int(s)) if s.isdigit() else (1, s.casefold()) for s in re.split(r"(\d+)", str(value)))


@dataclass
class Asset:
    name: str
    kind: str
    source: object = None
    size: int = 0
    details: str = ""
    payload: bytes | None = None
    edit_target: dict | None = None

    def read(self) -> bytes:
        if self.payload is not None:
            return self.payload
        if isinstance(self.source, Path):
            return self.source.read_bytes()
        return self.source.load()

    def image(self) -> Image.Image:
        if isinstance(self.source, ImageFile) and self.source.from_ifs:
            # The usual load() encodes PNG, which preview would decode again.
            size, pixels = self.source.load(raw_pixels=True)
            return Image.frombytes("RGBA", size, pixels)
        if isinstance(self.source, ImageCanvas):
            image = Image.new("RGBA", self.source.img_size)
            for sprite in self.source.images:
                image.paste(Asset(sprite.name, "图片", sprite).image(), (sprite.imgrect[0], sprite.imgrect[2]))
            return image
        if callable(getattr(self.source, "image", None)):
            return self.source.image().convert("RGBA")
        return Image.open(BytesIO(self.read())).convert("RGBA")


@dataclass
class Package:
    path: Path
    assets: list[Asset] = field(default_factory=list)
    archive: object = None
    animations: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: dict = field(default_factory=dict)
    atlas_regions: dict = field(default_factory=dict)
    format: str = "single"
    source_files: list = field(default_factory=list)
    _image_cache: OrderedDict = field(default_factory=OrderedDict)

    def close(self):
        self._image_cache.clear()
        if self.archive:
            if isinstance(self.archive, IFS):
                seen = set()
                def close_archive(archive):
                    if id(archive) in seen:
                        return
                    seen.add(id(archive))
                    for folder in archive.tree.all_folders:
                        for shared in folder.supers:
                            close_archive(shared)
                    archive.close()
                close_archive(self.archive)
            else:
                self.archive.close()

    def texture(self, name: str):
        name = name.removesuffix(".png")
        if name in self._image_cache:
            self._image_cache.move_to_end(name)
            return self._image_cache[name]
        obj = self.files.get(f"tex/{name}.png")
        if obj is None:
            return None
        im = obj.image()
        # Bounded decoded texture memory; the archive stays lazily loaded.
        self._image_cache[name] = im
        while sum(x.width * x.height * 4 for x in self._image_cache.values()) > 128 * 1024 * 1024 and len(self._image_cache) > 1:
            self._image_cache.popitem(last=False)
        return im


def media_suffix(data: bytes, fallback=".bin"):
    if data[:4] == b'\x00\x00\x01\xba':
        return '.mpg'
    if data[:4] == b"RIFF":
        return ".wav" if data[8:12] == b"WAVE" else ".avi"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:4] == b"\x30\x26\xb2\x75":
        head = data[:65536]
        video = b"\xc0\xef\x19\xbc\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b"
        audio = b"\x40\x9e\x69\xf8\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b"
        return ".wma" if audio in head and video not in head else ".wmv"
    if data[4:8] == b"ftyp":
        return ".mp4"
    return fallback


def unwrap_media(data: bytes):
    if data.startswith(b"S3V0") and len(data) >= 32:
        off = struct.unpack_from("<I", data, 4)[0]
        if not 16 <= off < len(data):
            raise ValueError("S3V 头无效")
        return data[off:]
    return data


def audio_assets(data: bytes, name: str) -> list[Asset]:
    """2DX table entries wrap RIFF; SD9 wraps one RIFF; S3P stores ASF slices."""
    from .audio_crypto import decode_2dx
    data = decode_2dx(data) if data.startswith(b'%') else data
    result = []
    if data.startswith(b"SD9\x00"):
        start = struct.unpack_from("<I", data, 4)[0]
        if data[start:start + 4] != b"RIFF":
            raise ValueError("SD9 中未找到 RIFF 音频")
        end = start + 8 + struct.unpack_from("<I", data, start + 4)[0]
        if end > len(data):
            raise ValueError("SD9 音频被截断")
        return [Asset(Path(name).stem + ".wav", "音频", payload=data[start:end], edit_target={"kind": "track", "index": 0})]
    if data.startswith(b"S3P0"):
        count = struct.unpack_from("<I", data, 4)[0]
        if not 0 < count <= 100000 or 8 + count * 8 > len(data):
            raise ValueError("S3P 索引无效")
        for i in range(count):
            off, size = struct.unpack_from("<II", data, 8 + i * 8)
            if off < 8 + count * 8 or off + size > len(data):
                raise ValueError(f"S3P 第 {i + 1} 个音频范围无效")
            blob = unwrap_media(data[off:off + size])
            result.append(Asset(f"{Path(name).stem}_{i + 1:04}{media_suffix(blob, '.s3v')}", "音频", payload=blob, edit_target={"kind": "track", "index": i}))
        return result
    if len(data) >= 0x48:
        header, count = struct.unpack_from("<II", data, 0x10)
        if not 0 < count <= 100000 or 0x48 + count * 4 > len(data) or header < 0x48 + count * 4:
            raise ValueError("不支持此 2DX（可能为加密版本）")
        offsets = struct.unpack_from(f"<{count}I", data, 0x48)
        for i, off in enumerate(offsets):
            if off + 24 > len(data) or data[off:off + 4] != b"2DX9":
                raise ValueError(f"2DX 第 {i + 1} 个音频头无效")
            head, size = struct.unpack_from("<II", data, off + 4)
            start = off + head
            if head < 24 or start + size > len(data):
                raise ValueError("2DX 音频范围无效")
            blob = data[start:start + size]
            result.append(Asset(f"{Path(name).stem}_{i + 1:04}{media_suffix(blob, '.wav')}", "音频", payload=blob, edit_target={"kind": "track", "index": i}))
        return result
    raise ValueError("无法识别音频容器")


def open_package(path: Path, cancelled=lambda: False) -> Package:
    if path.suffix.lower()=='.moc3' or path.name.lower().endswith('.model3.json'):
        from .live2d import open_live2d
        return open_live2d(path)
    package = Package(path)
    package.source_files = [path]
    try:
        ext = path.suffix.lower()
        renamed_ifs = False
        if ext == ".bin":
            with path.open("rb") as file:
                renamed_ifs = file.read(4) == b"\x6c\xad\x8f\x89"
        if ext == ".ifs" or renamed_ifs:
            package.format = "ifs"
            package.archive = IFS(str(path), super_abort_if_bad=True)
            for f in package.archive.tree.all_files:
                if cancelled():
                    raise InterruptedError()
                name = f.full_path.replace("\\", "/").lstrip("/")
                if isinstance(f, ImageFile):
                    kind = "图片"
                    details = f"{f.img_size[0]} × {f.img_size[1]} · {f.format}"
                elif isinstance(f, ImageCanvas):
                    kind, details = "图集", f"{f.img_size[0]} × {f.img_size[1]}"
                else:
                    ext = Path(name).suffix.lower()
                    kind = "文件" if ext == ".ssp" else "音频包" if ext in {".2dx", ".sd9", ".s3p"} else "音频" if ext in AUDIO_EXT else "视频" if ext in MEDIA_EXT else "文件"
                    details = ""
                asset = Asset(name, kind, f, getattr(f, "size", 0) or 0, details, edit_target={"kind": "ifs"})
                package.assets.append(asset)
                package.files[name] = asset
            from .afp import load_animations
            package.animations, package.warnings = load_animations(package, cancelled)
        elif ext == ".ssp":
            from .ssp import SSPArchive
            package.format = "ssp"
            package.archive = SSPArchive(path, cancelled)
            package.assets = [Asset(f"{path.stem}_{t.sound_id:05d}.wav", "音频", t,
                                    t.wave_size, f"编号 {t.sound_id} · SD9")
                              for t in package.archive.tracks]
        elif path.suffix.lower() in {".2dx", ".sd9", ".s3p"}:
            package.format = "audio"
            package.assets = audio_assets(path.read_bytes(), path.name)
        elif path.suffix.lower() == ".bin":
            from .legacy import load_bin
            if load_bin(package, cancelled) is None:
                package.assets = [Asset(path.name, "文件", path, path.stat().st_size, edit_target={"kind": "single"})]
                package.warnings.append("此 BIN 格式暂不支持，可查看或导出原文件")
        elif path.suffix.lower() in {".idx", ".idr"}:
            from .legacy_idx import load_index
            load_index(package, path, cancelled)
        elif path.suffix.lower() in {".gcz", ".gcr"}:
            package.format = "gcz"
            from .legacy import LegacyImage
            from .legacy_codecs import decrypt_cbc_cts, gc_image, read_file
            def decode_tile():
                data = read_file(path)
                if path.suffix.lower() == ".gcr":
                    data = decrypt_cbc_cts(data, path.parent.name, 32)
                return gc_image(data, path.stem)[0]
            package.assets = [Asset(path.stem + ".png", "图片", LegacyImage(decode_tile), edit_target={"kind": "gcz"})]
        else:
            ext = path.suffix.lower()
            kind = "图片" if ext in IMAGE_EXT else "视频" if ext in MEDIA_EXT and catalog_kind(path) == "BGA" else "音频" if ext in AUDIO_EXT else "文件"
            package.assets = [Asset(path.name, kind, path, path.stat().st_size, edit_target={"kind": "single"})]
        package.assets.sort(key=lambda x: (x.kind, natural_key(x.name)))
        return package
    except BaseException:
        package.close()
        raise


@dataclass
class CatalogEntry:
    path: Path
    relative: str
    version: str
    kind: str
    size: int


def scan(root: Path, cancelled=lambda: False):
    import os
    entries = []
    excluded = {".git", ".venv", "node_modules", "__pycache__", "IIDXResourceViewer", "WINDOWS", "Documents and Settings", "src", "extras", "build", "dist"}
    folders=[(str(root),'',None)]
    while folders:
        if cancelled():
            raise InterruptedError()
        folder,prefix,inherited=folders.pop()
        try:
            with os.scandir(folder) as stream:children=list(stream)
        except OSError:continue
        has_model=any(p.name.lower().endswith('.model3.json') for p in children)
        for item in children:
            if cancelled():raise InterruptedError()
            name=item.name
            if item.is_symlink():continue
            relative=prefix+name
            try:
                if item.is_dir(follow_symlinks=False):
                    if name not in excluded and not name.startswith('.'):
                        version=inherited or (name if re.match(r'^\d{2}_',name) else None)
                        folders.append((item.path,relative+'/',version))
                    continue
            except OSError:continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_EXT and not name.lower().endswith('.model3.json'):
                continue
            if ext=='.moc3' and has_model:
                continue
            path = Path(item.path)
            try:
                entries.append(CatalogEntry(path,relative,inherited or root.name,catalog_kind(path),item.stat(follow_symlinks=False).st_size))
            except OSError:
                continue
    return sorted(entries, key=lambda e: natural_key(e.relative))
