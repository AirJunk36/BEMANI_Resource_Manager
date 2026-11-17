"""TXP2 BIN packages and old IIDX sidecar manifests, opened read-only."""
from collections import OrderedDict
from io import BytesIO
import json
from pathlib import Path
import re

from .legacy_codecs import BinaryReader, read_file, tdxt_header, tdxt_image, texture_blob, txp2_bytes


class LegacyImage:
    def __init__(self, decode):
        self.decode = decode

    def image(self):
        return self.decode()

    def load(self):
        output = BytesIO()
        self.image().save(output, "PNG")
        return output.getvalue()


class TextureStore:
    def __init__(self, blobs, flags):
        self.blobs, self.flags = blobs, flags
        self.cache = OrderedDict()
        self.cache_bytes=0

    def image(self, index):
        if index not in self.cache:
            self.cache[index] = tdxt_image(texture_blob(self.blobs[index], self.flags))
            image=self.cache[index];self.cache_bytes+=image.width*image.height*4
        self.cache.move_to_end(index)
        while len(self.cache)>1 and self.cache_bytes>96*1024*1024:
            _,old=self.cache.popitem(last=False);self.cache_bytes-=old.width*old.height*4
        return self.cache[index]

    def close(self):
        self.cache.clear()
        self.cache_bytes=0


def count_table(reader, count, offset, stride):
    if not 0 <= count <= 100000:
        raise ValueError("旧版资源索引数量超过上限")
    reader.span(offset, count * stride)
    return range(count)


def load_txp2(package, data, cancelled):
    from .resources import Asset
    from .afp import load_animations
    package.format = "txp2"

    r = BinaryReader(data, ">" if data[:4] == b"TXP2" else "<")
    if r.u32(12) != len(data):
        raise ValueError("TXP2 文件长度不匹配")
    header, flags = r.values("II", 16)
    specs = [(1, 2), (2, 1), (8, 2), (16, 1), (64, 2), (128, 1),
             (256, 2), (512, 1), (1024, 1), (2048, 2), (4096, 1),
             (8192, 2), (16384, 1), (32768, 1), (65536, 1), (131072, 1)]
    if flags & ~0x7FFFF:
        raise ValueError(f"未知的 TXP2 标志：0x{flags:x}")
    sections, cursor = {}, 24
    for bit, words in specs:
        if flags & bit:
            sections[bit] = r.values("I" * words, cursor)
            cursor += words * 4
    if header != cursor:
        raise ValueError("TXP2 头长度与标志不匹配")
    scrambled = bool(flags & 32)

    def names(bit):
        off = sections.get(bit, (0,))[0]
        if not off:
            return {}
        r.span(off, 28)
        if r.span(off, 4) not in {b"PMAN", b"NAMP"}:
            raise ValueError("TXP2 名称索引标识无效")
        count, table = r.u32(off + 16), r.u32(off + 24)
        mapping = {}
        for i in count_table(r, count, table, 12):
            _, index, name = r.values("III", table + i * 12)
            if index >= count or index in mapping:
                raise ValueError("TXP2 名称索引编号无效")
            mapping[index] = r.string(name, scrambled)
        return mapping

    def blobs(bit):
        count, table = sections.get(bit, (0, 0))
        entries = []
        for i in count_table(r, count, table, 12):
            if cancelled():
                raise InterruptedError()
            name, length, offset = r.values("III", table + i * 12)
            entries.append((r.string(name, scrambled) or f"entry_{i:04}", r.span(offset, length)))
        return entries

    textures = blobs(1)
    store = TextureStore([blob for _, blob in textures], flags)
    package.archive = store
    sizes = []
    texture_names = names(2)
    for i, (name, blob) in enumerate(textures):
        width, height, fmt = tdxt_header(texture_blob(blob, flags, 64))
        sizes.append((width, height))
        source = LegacyImage(lambda i=i: store.image(i))
        asset = Asset(f"atlas/{name}.png", "图集", source, len(blob), f"{width} × {height} · TDXT 0x{fmt:02x}", edit_target={"kind": "txp2_image", "index": i})
        package.assets.append(asset)
        # Some animations refer directly to an atlas instead of a cell.
        package.files[f"tex/{texture_names.get(i, name)}.png"] = asset

    cell_names = names(16)
    count, table = sections.get(8, (0, 0))
    for i in count_table(r, count, table, 10):
        tex, x0, y0, x1, y1 = r.values("5H", table + i * 10)
        if tex >= len(textures):
            raise ValueError("TXP2 裁切图引用了不存在的纹理")
        rect = (x0 // 2, y0 // 2, x1 // 2, y1 // 2)
        if not 0 <= rect[0] < rect[2] <= sizes[tex][0] or not 0 <= rect[1] < rect[3] <= sizes[tex][1]:
            raise ValueError("TXP2 裁切区域超出纹理")
        name = cell_names.get(i, f"cell_{i:04}")
        source = LegacyImage(lambda tex=tex, rect=rect: store.image(tex).crop(rect))
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        asset = Asset(f"tex/{name}.png", "图片", source, details=f"{width} × {height}", edit_target={"kind": "txp2_image", "index": tex, "rect": rect})
        package.assets.append(asset)
        package.files[asset.name] = asset
        package.atlas_regions[name] = (sizes[tex], (rect[0], rect[1]))

    for folder, bit in [("afp", 2048), ("geo", 8192)]:
        for index, (name, blob) in enumerate(blobs(bit)):
            asset = Asset(f"{folder}/{name}", "文件", payload=blob, size=len(blob), edit_target={"kind": "txp2_blob", "bit": bit, "index": index})
            package.assets.append(asset)
            package.files[asset.name] = asset
    bsi = sections.get(131072, (0,))[0]
    afps = [a for a in package.assets if a.name.startswith("afp/")]
    if bsi:
        for i in count_table(r, len(afps), bsi, 12):
            _, length, offset = r.values("III", bsi + i * 12)
            package.files["afp/bsi/" + afps[i].name[4:]] = Asset("bsi", "文件", payload=r.span(offset, length))
    package.files["magic"] = Asset("magic", "文件", payload=b"NGPF" if r.endian == ">" else b"FPGN")
    package.animations, warnings = load_animations(package, cancelled)
    package.warnings.extend(warnings)
    if flags & 65536:
        package.warnings.append("游戏文字排版暂不支持")
    return package


def sibling(directory, name):
    """Game VFS paths are labels; resolve only their leaf in the same folder."""
    leaf = name.replace("\\", "/").split("/")[-1]
    if leaf in {"", ".", ".."} or "\0" in leaf:
        raise ValueError("素材关联文件名无效")
    candidate = directory / leaf
    if not candidate.is_file():
        candidate = next((p for p in directory.iterdir() if p.name.casefold() == leaf.casefold() and p.is_file()), candidate)
    # Do not follow a manifest's symlink outside the selected game directory.
    if not candidate.resolve().is_relative_to(directory.resolve()):
        raise ValueError("素材关联文件指向目录以外的位置")
    return candidate


def load_manifest(package, data, cancelled):
    from .resources import Asset, AUDIO_EXT, VIDEO_EXT
    from .legacy_idx import load_index

    package.format = 'manifest'
    r = BinaryReader(data)
    version, count, kind = r.values("III", 4)
    if count > 100000 or len(data) < 16 or not count:
        raise ValueError("IIDX BIN 文件索引无效")
    stride, remainder = divmod(len(data) - 16, count)
    if remainder or stride not in {36, 52}:
        raise ValueError("未知的 IIDX BIN 索引记录长度")
    entries = []
    for i in count_table(r, count, 16, stride):
        raw = r.span(16 + i * stride, 32)
        name = raw.split(b"\0")[0].decode("shift_jis", "replace")
        entries.append(name)
    index = next((name for name in entries if name.lower().endswith((".idx", ".idr"))), None)
    if index:
        path = sibling(package.path.parent, index)
        if path.is_file():
            load_index(package, path, cancelled)
        else:
            package.warnings.append(f"缺少关联素材：{path.name}，请保留原目录结构")
    # Integrity lists also occur beside sound and chart data; keep those readable.
    summary = json.dumps({"格式": "IIDX 文件索引", "版本": version, "类型": kind, "关联文件": entries}, ensure_ascii=False, indent=2).encode("utf8")
    package.assets.append(Asset("manifest.json", "文件", payload=summary))
    package.assets.append(Asset("original/" + package.path.name, "文件", package.path, len(data)))
    if not index:
        # Osprey/Owl contain a directory, not embedded payloads. Keep linked
        # banks and movies lazy, just as when opening those files directly.
        for name in entries:
            if cancelled():raise InterruptedError()
            path = sibling(package.path.parent,name)
            if not path.is_file():
                package.warnings.append(f"缺少关联素材：{path.name}，请保留原目录结构")
                continue
            ext=path.suffix.lower()
            kind='音频包' if ext in {'.2dx','.sd9','.s3p'} else '音频' if ext in AUDIO_EXT else '视频' if ext in VIDEO_EXT else '文件'
            package.assets.append(Asset(path.name,kind,path,path.stat().st_size))
            package.source_files.append(path)
    return package


def load_viewer_list(package, data):
    """9–12 style aepview.bin is a scene list, not a pixel container."""
    from .resources import Asset
    package.format = "catalog"

    # Only resolve observed labels that actually name sibling scene folders;
    # the numeric control tables differ between viewer builds.
    labels = {m.group()[:-1].decode("ascii") for m in re.finditer(rb"[A-Za-z0-9_]{2,64}\x00", data)}
    entries = []
    for name in sorted(labels):
        folder = package.path.parent / name
        if folder.is_dir() and folder.resolve().is_relative_to(package.path.parent.resolve()):
            index = folder / "system.idx"
            if index.is_file():
                entries.append({"场景": name, "资源": str(index)})
    summary = json.dumps({"格式": "旧版素材场景列表", "可打开的关联素材": entries}, ensure_ascii=False, indent=2).encode("utf8")
    package.assets.append(Asset("00_场景列表.json", "文件", payload=summary))
    package.assets.append(Asset("original/" + package.path.name, "文件", package.path, len(data)))
    package.warnings.append("此 BIN 仅含场景列表，请打开关联的 system.idx / GCZ")
    return package


def load_bin(package, cancelled):
    data = read_file(package.path)
    if data[:4] == b"IIDX":
        return load_manifest(package, data, cancelled)
    if data[:2] == b"AN":
        return load_viewer_list(package, data)
    decoded = txp2_bytes(package.path, data)
    if decoded is not None:
        return load_txp2(package, decoded, cancelled)
    return None
