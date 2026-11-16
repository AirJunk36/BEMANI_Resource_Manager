"""Container writers used exclusively on new temporary copies."""
from copy import deepcopy
import hashlib
from io import BytesIO
import os
from pathlib import Path
import struct
import zlib

from ifstools.handlers import lz77
from ifstools.handlers.generic_file import GenericFile
from ifstools.handlers.image_decoders import image_formats
from ifstools.handlers.image_file import ImageFile
from ifstools.handlers.tex_folder import ImageCanvas
from kbinxml import KBinXML
from lxml import etree
from PIL import Image

from .legacy_codecs import (BinaryReader, decrypt_cbc_cts, gc_image, inflate,
                            named_key, tdxt_header, texture_blob, txp2_bytes)


def encrypt_cbc_cts(plain, name, size=16):
    from Crypto.Cipher import AES
    if len(plain) < 16:
        raise ValueError("加密素材太短")
    iv = os.urandom(16)
    tail = len(plain) % 16
    cipher = AES.new(named_key(name, size), AES.MODE_CBC, iv).encrypt(plain + bytes((-len(plain)) % 16))
    if tail:
        cipher = cipher[:-32] + cipher[-16:] + cipher[-32:-16][:tail]
    return iv + cipher


def compress_legacy(plain, prefix=True):
    """Translate native AVS distance tokens to the legacy absolute ring form."""
    coded = lz77.compress(plain)
    output = bytearray(struct.pack("<I", len(plain)) if prefix else b"")
    cursor, written, ring = 0, 0, 4078
    while written < len(plain):
        flags = coded[cursor]
        output.append(flags)
        cursor += 1
        for bit in range(8):
            if written >= len(plain):
                break
            if flags & (1 << bit):
                output.append(coded[cursor]); cursor += 1
                amount = 1
            else:
                token = int.from_bytes(coded[cursor:cursor + 2], "big")
                cursor += 2
                distance, amount = token >> 4, (token & 15) + 3
                if not distance:
                    raise ValueError("压缩器提前结束")
                position = (ring - distance) & 4095
                output.extend((position & 255, ((position >> 4) & 240) | (amount - 3)))
            ring = (ring + amount) & 4095
            written += amount
    return bytes(output)


def encode_image_for_ifs(source, image):
    encoder = image_formats.get(source.format, {}).get("encoder")
    if encoder is None:
        raise ValueError(f"暂不能写回 IFS 像素格式 {source.format}")
    raw = encoder(source, image.convert("RGBA"))
    if source.compress == "avslz":
        coded = lz77.compress(raw)
        return struct.pack(">II", len(raw), len(coded)) + coded
    return raw


def ifs_entries(archive):
    """Map the stored manifest paths to resolved files, including super files."""
    def walk(folder, node, path):
        for name, sub in folder.folders.items():
            child = node.find(sub.packed_name)
            if child is not None:
                yield from walk(sub, child, path + (child.tag,))
        for name, item in folder.files.items():
            if isinstance(item, ImageCanvas):
                continue
            child = node.find(item.packed_name)
            if child is None:
                from ifstools.handlers.node import Node
                child = node.find(Node.sanitize_name(name))
            if child is None:
                raise ValueError(f"无法定位 IFS 索引项：{name}")
            yield path + (child.tag,), item
    return list(walk(archive.tree, archive.manifest.xml_doc, ()))


def rewrite_ifs(archive, replacements, destination):
    """Preserve unknown metadata and untouched encoded bytes; localize super refs."""
    tree = deepcopy(archive.manifest.xml_doc)
    payload = BytesIO()
    for path, source in ifs_entries(archive):
        element = tree
        for part in path:
            element = element.find(part)
        raw = replacements.get(id(source))
        if raw is None:
            raw = GenericFile._load_from_ifs(source, convert_kbin=False)
        values = list(map(int, element.text.split()))
        values[:2] = [payload.tell(), len(raw)]
        element.text = " ".join(map(str, values))
        for child in list(element):
            if child.tag == "i":
                element.remove(child)
        payload.write(raw)
        payload.write(bytes((-payload.tell()) % 16))
    # All resolved entries have been localized, so a saved copy has no dependency
    # on files outside the output folder.
    for node in list(tree.iter("_super_")):
        node.getparent().remove(node)
    data = payload.getvalue()
    info = tree.find("_info_")
    if info is not None:
        for name, text in [("md5", hashlib.md5(data).hexdigest()), ("size", str(len(data)))]:
            child = info.find(name)
            if child is not None:
                child.text = text
    flags = int(archive.flags)
    manifest = KBinXML(tree)
    xml = manifest.to_binary(encoding=archive.manifest.encoding, compressed=not bool(flags & 4))
    header_size = 36 if flags & 2 else 20
    padding = bytes((-(header_size + len(xml))) % 16)
    body = xml + padding
    memory_size = KBinXML(xml).mem_size
    header = struct.pack(">IHHIII", 0x6CAD8F89, flags, flags ^ 0xffff, archive.time,
                         memory_size, header_size + len(body))
    if flags & 2:
        header += hashlib.md5(body).digest()
    with Path(destination).open("xb") as output:
        output.write(header); output.write(body); output.write(data)


def encode_16(image, fmt):
    rgba = image.convert("RGBA").tobytes()
    output = bytearray(image.width * image.height * 2)
    for offset in range(0, len(rgba), 4):
        r, g, b, a = rgba[offset:offset + 4]
        if fmt == 0x1F:
            value = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
        elif fmt == 0x0B:
            value = ((r * 31 + 127) // 255 << 11) | ((g * 63 + 127) // 255 << 5) | ((b * 31 + 127) // 255)
        else:
            value = (0x8000 if a >= 128 else 0) | ((r * 31 + 127) // 255 << 10) | ((g * 31 + 127) // 255 << 5) | ((b * 31 + 127) // 255)
        struct.pack_into("<H", output, offset // 2, value)
    return bytes(output)


def encode_tdxt(original, image):
    width, height, fmt = tdxt_header(original)
    if image.size != (width, height):
        raise ValueError("图集尺寸发生变化")
    if fmt in {0x10, 0x20}:
        pixels = image.convert("RGBA").tobytes("raw", "BGRA")
    elif fmt == 0x15:
        pixels = image.convert("RGBA").tobytes("raw", "ARGB")
    elif fmt == 0x0E:
        pixels = image.convert("RGB").tobytes()
    elif fmt == 0x01:
        pixels = image.convert("L").tobytes()
    elif fmt in {0x0B, 0x13, 0x1F}:
        pixels = encode_16(image, fmt)
    elif fmt in {0x16, 0x1A}:
        from ifstools.handlers.image_decoders import _native
        if _native is None:
            raise ValueError("写回 DXT 纹理需要原生图像编码库")
        pixels = _native.encode_dxt(image.convert("RGBA").tobytes(), width, height, "dxt1" if fmt == 0x16 else "dxt5")
    else:
        raise ValueError(f"暂不能写回 TDXT 像素格式 0x{fmt:02x}")
    head = bytearray(original[:64])
    endian = "<" if head[:4] == b"TDXT" else ">"
    old_length = struct.unpack_from(endian + "I", head, 12)[0]
    struct.pack_into(endian + "I", head, 12, len(pixels) if old_length + 64 == len(original) else len(pixels) + 64)
    return bytes(head) + pixels


def txp2_sections(data):
    r = BinaryReader(data, ">" if data[:4] == b"TXP2" else "<")
    flags, cursor, sections = r.u32(20), 24, {}
    for bit, words in [(1,2),(2,1),(8,2),(16,1),(64,2),(128,1),(256,2),(512,1),
                       (1024,1),(2048,2),(4096,1),(8192,2),(16384,1),(32768,1),(65536,1),(131072,1)]:
        if flags & bit:
            sections[bit] = r.values("I" * words, cursor)
            cursor += words * 4
    return r, flags, sections


def rewrite_txp2(package, target, replacement, destination):
    stored = package.path.read_bytes()
    original = txp2_bytes(package.path, stored)
    r, flags, sections = txp2_sections(original)
    bit = 1 if target["kind"] == "txp2_image" else target["bit"]
    count, table = sections[bit]
    index = target["index"]
    if not 0 <= index < count:
        raise ValueError("TXP2 替换索引无效")
    record = table + index * 12
    _, length, offset = r.values("III", record)
    if bit == 1:
        blob = r.span(offset, length)
        tdxt = texture_blob(blob, flags)
        image = package.archive.image(index).copy()
        if "rect" in target:
            image.paste(replacement, tuple(target["rect"][:2]))
        else:
            image = replacement
        encoded = encode_tdxt(tdxt, image)
        if blob[:4] in {b"TDXT", b"TXDT"}:
            replacement = encoded
        elif flags & 0x40000:
            coded = lz77.compress(encoded)
            replacement = struct.pack(">II", len(encoded), len(coded)) + coded
        elif flags & 4:
            coded = compress_legacy(encoded, prefix=False)
            replacement = struct.pack(">II", len(encoded), len(coded)) + coded
        else:
            replacement = struct.pack(">II", len(encoded), len(encoded)) + encoded
    data = bytearray(original)
    data.extend(bytes((-len(data)) % 4))
    new_offset = len(data)
    data.extend(replacement)
    data.extend(bytes((-len(data)) % 4))
    if len(data) >= 2**32:
        raise ValueError("TXP2 替换结果超过格式上限")
    struct.pack_into(r.endian + "II", data, record + 4, len(replacement), new_offset)
    struct.pack_into(r.endian + "I", data, 12, len(data))
    result = bytes(data)
    if stored[:4] not in {b"TXP2", b"2PXT"}:
        result = encrypt_cbc_cts(result, package.path.name)
    Path(destination).write_bytes(result)


def gc_layers(data, path):
    from Crypto.Cipher import Blowfish
    aes = path.suffix.lower() == ".gcr"
    if aes:
        data = decrypt_cbc_cts(data, path.parent.name, 32)
    bf_key = None
    try:
        plain = inflate(data)
        if plain[:3] != b"GC ":
            raise ValueError("非 GC 数据")
    except ValueError:
        seed = path.stem.encode("shift_jis").ljust(24, b"\0")[:24]
        key = Blowfish.new(b"omE872M2akmRkERh4177S", Blowfish.MODE_CBC, b"tukupypy").encrypt(seed)
        bf_key = (key[:-1] + b"\0").split(b"\0")[0]
        plain = inflate(Blowfish.new(bf_key, Blowfish.MODE_CBC, b"tukupypy").decrypt(data))
    return plain, aes, bf_key


def encode_gc(path, image):
    from Crypto.Cipher import Blowfish
    plain, aes, bf_key = gc_layers(path.read_bytes(), path)
    r = BinaryReader(plain, ">")
    if image.size != r.values("HH", 12):
        raise ValueError("GC 图集尺寸发生变化")
    pixels = image.convert("RGBA").tobytes("raw", "BGRA") if plain[19] == 32 else encode_16(image, 0x13)
    header = bytearray(plain[:24])
    struct.pack_into(">I", header, 4, 24 + len(pixels))
    struct.pack_into(">I", header, 20, len(pixels))
    data = compress_legacy(bytes(header) + pixels)
    if bf_key is not None:
        data = Blowfish.new(bf_key, Blowfish.MODE_CBC, b"tukupypy").encrypt(data + bytes((-len(data)) % 8))
    if aes:
        data = encrypt_cbc_cts(data, path.parent.name, 32)
    return data


def update_integrity_manifest(data, changed):
    """Keep filename hashes/unknown fields, update recognized data CRC/size slots."""
    if data[:4] != b"IIDX":
        return data
    result = bytearray(data)
    count = struct.unpack_from("<I", data, 8)[0]
    if not count:
        return data
    stride, remainder = divmod(len(data) - 16, count)
    if remainder or stride != 52:
        return data
    for i in range(count):
        at = 16 + i * stride
        name = data[at:at + 32].split(b"\0")[0].decode("shift_jis", "replace")
        if name not in changed:
            continue
        before, after = changed[name]
        if struct.unpack_from("<I", data, at + 36)[0] == zlib.crc32(before):
            struct.pack_into("<I", result, at + 36, zlib.crc32(after))
        for offset in [44, 48]:
            if struct.unpack_from("<I", data, at + offset)[0] == len(before):
                struct.pack_into("<I", result, at + offset, len(after))
    return bytes(result)
