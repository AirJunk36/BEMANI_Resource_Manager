"""Transactional replacement sessions. Only owned temporary copies are writable."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from ifstools.handlers.image_file import ImageFile
from ifstools.handlers.tex_folder import ImageCanvas
from kbinxml import KBinXML
from PIL import Image

from .legacy import sibling
from .legacy_codecs import decrypt_cbc_cts, gc_image, txp2_bytes
from .packing import (encode_gc, encode_image_for_ifs, encrypt_cbc_cts, rewrite_ifs,
                      rewrite_txp2, update_integrity_manifest)
from .replacement_media import MediaConverter, replace_audio_track, wrap_s3v
from .resources import audio_assets, media_suffix, open_package

VIDEO_GUID = b"\xc0\xef\x19\xbc\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b"


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_sources(package, folder, *, primary=True):
    seen = {}
    for source in package.source_files:
        source = Path(source)
        if not primary and source == package.path:
            continue
        if not source.is_file():
            raise ValueError("缺少关联素材：" + source.name)
        previous = seen.setdefault(source.name.casefold(), source)
        if previous != source:
            raise ValueError("关联文件名称冲突：" + source.name)
        destination = folder / source.name
        if not destination.exists():
            shutil.copyfile(source, destination)


def expand_tracks(package, parent):
    existing = [a for a in package.assets if a.edit_target and a.edit_target.get("kind") == "ifs_track" and a.edit_target.get("parent") == parent.name]
    if existing:
        return existing
    tracks = audio_assets(parent.read(), parent.name)
    for track in tracks:
        track.edit_target = {"kind": "ifs_track", "parent": parent.name, "index": track.edit_target["index"]}
    package.assets.extend(tracks)
    return tracks


def audio_type(data, name=None):
    if data[:4] in {b"SD9\0", b"S3P0"}:
        return data[:4]
    from .audio_crypto import KEYS, named_2dx
    if named_2dx(data, name):
        return b'AES-2DX'
    if data[:4] in KEYS:
        return data[:4]
    if len(data) >= 0x48 and not data.startswith((b"%", b"S3V0")):
        # Full validation is done by audio_assets before this result is used.
        return b"2DX9"
    return b"unknown"


class ReplacementWriter:
    def __init__(self, package, folder):
        self.package, self.folder = package, Path(folder)
        self.media = MediaConverter(folder)

    def payload(self, asset, source):
        original = asset.read()
        if asset.kind in {"音频", "视频"}:
            suffix = Path(asset.name).suffix or media_suffix(original)
            if asset.kind == "视频" or VIDEO_GUID in original[:65536]:
                output = self.media.video(original, source, suffix)
            else:
                output = self.media.audio(original, source, suffix)
            return wrap_s3v(original, output.read_bytes())
        data = Path(source).read_bytes()
        if asset.kind == "音频包":
            audio_assets(data, asset.name)
            kind=audio_type(original, asset.name)
            if kind != audio_type(data, Path(source).name):
                raise ValueError("整包替换需同格式；WAV/MP3 请替换包内单条音频。")
            if kind==b'AES-2DX':
                from .audio_crypto import decode_2dx
                data=encrypt_cbc_cts(decode_2dx(data,Path(source).name),Path(asset.name).name)
        if asset.name.lower().endswith(".xml") and KBinXML.is_binary_xml(original) and not KBinXML.is_binary_xml(data):
            data = KBinXML(data).to_binary(encoding=KBinXML(original).encoding)
        return data

    def ifs(self, asset, source, destination):
        replacements = {}
        if asset.edit_target["kind"] == "ifs_track":
            parent = next(a for a in self.package.assets if a.name == asset.edit_target["parent"] and a.kind == "音频包")
            replacements[id(parent.source)] = replace_audio_track(parent.read(), asset.edit_target["index"], source, self.media, parent.name)
        elif isinstance(asset.source, ImageCanvas):
            canvas = asset.source
            image = self.media.image(source, canvas.img_size)
            for sprite in canvas.images:
                x0, x1, y0, y1 = sprite.imgrect
                replacements[id(sprite)] = encode_image_for_ifs(sprite, image.crop((x0, y0, x1, y1)))
        elif isinstance(asset.source, ImageFile):
            image = self.media.image(source, asset.source.img_size)
            replacements[id(asset.source)] = encode_image_for_ifs(asset.source, image)
        else:
            replacements[id(asset.source)] = self.payload(asset, source)
        rewrite_ifs(self.package.archive, replacements, destination)

    def legacy(self, asset, source, destination):
        package, target = self.package, asset.edit_target
        copy_sources(package, self.folder)
        changed = {}
        if target["kind"] == "legacy_raw":
            path = self.folder / target["file"]
            original = path.read_bytes()
            updated = Path(source).read_bytes()
            path.write_bytes(updated)
            changed[path.name] = original, updated
        else:
            atlas = package.archive
            if target["kind"] == "legacy_atlas":
                index = target["index"]
                image = atlas.image(index).copy()
                updates = {index: self.media.image(source, image.size)}
            else:
                x, y, width, height = target["rect"]
                image = self.media.image(source, (width, height))
                updates = {}
                for index in range(y // 1024, (y + height - 1) // 1024 + 1):
                    tile = atlas.image(index).copy()
                    ox, oy = atlas.cache[index][1]
                    top, bottom = max(y, index * 1024 + oy), min(y + height, index * 1024 + oy + tile.height)
                    left, right = max(x, ox), min(x + width, ox + tile.width)
                    if right > left and bottom > top:
                        tile.paste(image.crop((left - x, top - y, right - x, bottom - y)), (left - ox, top - index * 1024 - oy))
                    updates[index] = tile
            for index, image in updates.items():
                original = sibling(atlas.folder, atlas.paths[index])
                if not original.exists():
                    original = sibling(atlas.folder, original.with_suffix(".gcr").name)
                data = encode_gc(original, image)
                (self.folder / original.name).write_bytes(data)
                changed[original.name] = original.read_bytes(), data
        for path in package.source_files:
            path = Path(path)
            if path.suffix.lower() == ".bin":
                original = path.read_bytes()
                updated = update_integrity_manifest(original, changed)
                if updated != original:
                    (self.folder / path.name).write_bytes(updated)

    def single(self, asset, source, destination):
        package = self.package
        if package.format == "gcz":
            image = asset.image()
            updated = self.media.image(source, image.size)
            destination.write_bytes(encode_gc(package.path, updated))
        elif asset.kind in {"音频", "视频"}:
            with package.path.open("rb") as file:
                header = file.read(32)
            if asset.kind == "视频" or VIDEO_GUID in header:
                output = self.media.video(package.path, source, package.path.suffix)
            else:
                output = self.media.audio(package.path, source, package.path.suffix)
            if header[:4] == b"S3V0":
                # S3V headers are small; avoid loading the original BGA into memory.
                offset = int.from_bytes(header[4:8], "little")
                with package.path.open("rb") as file:
                    template = file.read(offset)
                destination.write_bytes(wrap_s3v(template, output.read_bytes()))
            else:
                shutil.copyfile(output, destination)
        elif asset.kind in {"图片", "图集"}:
            if package.path.suffix.lower() == ".gif" and Path(source).suffix.lower() == ".gif":
                with Image.open(source) as image:
                    image.verify()
                shutil.copyfile(source, destination)
            else:
                image = self.media.image(source, asset.image().size)
                if package.path.suffix.lower() in {".jpg", ".jpeg", ".bmp"}:
                    image = image.convert("RGB")
                image.save(destination)
        else:
            if asset.name.lower().endswith(".xml"):
                destination.write_bytes(self.payload(asset, source))
            else:
                shutil.copyfile(source, destination)

    def whole(self, source, destination):
        original = self.package
        if original.format == "gcz" and Path(source).suffix.lower() in {".gcz", ".gcr"}:
            data = Path(source).read_bytes()
            if Path(source).suffix.lower() == ".gcr":
                data = decrypt_cbc_cts(data, Path(source).parent.name, 32)
            image = gc_image(data, Path(source).stem)[0]
            size = original.assets[0].image().size
            if image.size != size:
                image = image.resize(size, Image.Resampling.LANCZOS)
                self.media.messages.append(f"图片：{size[0]} × {size[1]}")
            destination.write_bytes(encode_gc(original.path, image))
            return
        if original.format in {"single", "gcz"}:
            self.single(original.assets[0], source, destination)
            return
        candidate = open_package(Path(source))
        try:
            if candidate.format != original.format:
                raise ValueError("整包替换需同格式；转换请先选择包内素材。")
            if candidate.format == "ifs":
                rewrite_ifs(candidate.archive, {}, destination)
            elif candidate.format == "txp2":
                before = original.path.read_bytes()
                decoded = txp2_bytes(Path(source), Path(source).read_bytes())
                destination.write_bytes(decoded if before[:4] in {b"TXP2", b"2PXT"} else encrypt_cbc_cts(decoded, original.path.name))
            elif candidate.format == "legacy":
                if candidate.path.parent.name != original.path.parent.name:
                    raise ValueError("请选择同名素材目录，或逐张替换图片。")
                copy_sources(candidate, self.folder)
                if candidate.path.name != destination.name:
                    shutil.copyfile(candidate.path, destination)
            else:
                if original.format == "audio":
                    before, after = original.path.read_bytes(), Path(source).read_bytes()
                    kind = audio_type(before, original.path.name)
                    if kind != audio_type(after, Path(source).name):
                        raise ValueError("整包替换需要同格式音频包。")
                    if kind == b'AES-2DX':
                        from .audio_crypto import decode_2dx
                        destination.write_bytes(encrypt_cbc_cts(decode_2dx(after, Path(source).name), original.path.name))
                        return
                shutil.copyfile(source, destination)
        finally:
            candidate.close()

    def write(self, asset, source, destination, whole=False):
        if whole:
            self.whole(source, destination)
            return
        target = asset.edit_target
        if not target:
            raise ValueError("索引说明不可替换，请选择原素材或整个文件。")
        if self.package.format == "ifs":
            self.ifs(asset, source, destination)
        elif self.package.format == "txp2":
            replacement = self.media.image(source, asset.image().size) if target["kind"] == "txp2_image" else Path(source).read_bytes()
            rewrite_txp2(self.package, target, replacement, destination)
        elif self.package.format == "audio":
            data = replace_audio_track(self.package.path.read_bytes(), target["index"], source, self.media, self.package.path.name)
            destination.write_bytes(data)
        elif self.package.format == "legacy":
            self.legacy(asset, source, destination)
        else:
            self.single(asset, source, destination)


@dataclass
class EditStep:
    folder: Path
    selected_name: str | None
    target: dict | None
    source: str
    messages: list


@dataclass
class EditSession:
    original: Path
    originals: dict
    steps: list = field(default_factory=list)

    @property
    def path(self):
        return self.steps[-1].folder / self.original.name if self.steps else self.original


class Editor:
    def __init__(self, cache):
        self.cache, self.sessions = Path(cache), {}

    def session(self, original):
        return self.sessions.get(str(original))

    def path(self, original):
        session = self.session(original)
        return session.path if session else original

    def replace(self, original, package, asset, source, whole=False):
        source = Path(source).resolve()
        if not source.is_file():
            raise ValueError("替换文件不存在")
        session = self.session(original)
        new_session = session is None
        if new_session:
            session = EditSession(original, {str(path): checksum(path) for path in package.source_files})
        outer = self.cache / ("edit-" + uuid.uuid4().hex)
        # Preserve the original directory name used by IDR/GCR key derivation.
        folder = outer / original.parent.name
        folder.mkdir(parents=True)
        destination = folder / original.name
        writer = ReplacementWriter(package, folder)
        candidate = None
        try:
            writer.write(asset, source, destination, whole)
            candidate = open_package(destination)
            if any("缺少关联" in warning for warning in candidate.warnings):
                raise ValueError("缺少关联素材，请选择完整资源包。")
            name = asset.name if asset is not None and not whole else None
            target = dict(asset.edit_target) if asset is not None and asset.edit_target is not None else None
            if target and target["kind"] == "ifs_track":
                parent = next(a for a in candidate.assets if a.name == target["parent"] and a.kind == "音频包")
                expand_tracks(candidate, parent)
            step = EditStep(folder, name, target, str(source), writer.media.messages)
            session.steps.append(step)
            self.sessions[str(original)] = session
            return candidate, name, writer.media.messages
        except BaseException:
            if candidate:
                candidate.close()
            shutil.rmtree(outer, ignore_errors=True)
            raise

    def undo(self, original):
        session = self.session(original)
        if not session or not session.steps:
            raise ValueError("没有可撤销的替换")
        last = session.steps[-1]
        path = session.steps[-2].folder / original.name if len(session.steps) > 1 else original
        candidate = open_package(path)
        try:
            if last.target and last.target["kind"] == "ifs_track":
                parent = next(a for a in candidate.assets if a.name == last.target["parent"] and a.kind == "音频包")
                expand_tracks(candidate, parent)
        except BaseException:
            candidate.close()
            raise
        session.steps.pop()
        return candidate, last.selected_name

    def save(self, original, package, target):
        session = self.session(original)
        if not session or not session.steps:
            raise ValueError("请先替换素材。")
        target = Path(target)
        target.mkdir(exist_ok=False)
        try:
            # Legacy VFS paths and encryption keys include this directory name.
            output = target / original.parent.name if package.format in {"legacy", "gcz"} else target
            output.mkdir(exist_ok=True)
            copy_sources(package, output)
            report = {"source": str(original), "source_sha256": session.originals,
                      "files": [str((output / Path(p).name).relative_to(target)) for p in package.source_files],
                      "replacements": [{"asset": step.selected_name or "whole_file", "replacement": step.source, "details": step.messages} for step in session.steps]}
            record = target / "replacements.json"
            number = 2
            while record.exists():
                record = target / f"replacements_{number}.json"
                number += 1
            record.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
            return {"path": str(target), "primary": str(output / original.name), "files": report["files"]}
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
