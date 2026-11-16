"""Bounded, read-only codecs for pre-IFS IIDX graphics.

Format references: Radioo/573Renderer (LZSS/GC/AES-CTS) and
DragonMinded/bemaniutils (TXP2/TDXT). No game code or DLL is executed.
"""
from io import BytesIO
from pathlib import Path
import struct

from PIL import Image

MAX_BYTES = 256 * 1024 * 1024
MAX_PIXELS = 32 * 1024 * 1024
KEY_A = bytes.fromhex("40bc0b71fb2ed7203e19933162c8e4b5e87c8db1b8000764343930e156ff6a97")
KEY_B = bytes.fromhex("518bd90298cb9856ba95bac074fab0c057a4f3489ba1da1db81fc7d3a11989f2")


class BinaryReader:
    def __init__(self, data, endian="<"):
        self.data, self.endian = data, endian

    def span(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError(f"旧版资源数据越界：0x{offset:x} + {size}")
        return self.data[offset:offset + size]

    def values(self, fmt, offset):
        size = struct.calcsize(self.endian + fmt)
        self.span(offset, size)
        return struct.unpack_from(self.endian + fmt, self.data, offset)

    def u32(self, offset): return self.values("I", offset)[0]
    def u16(self, offset): return self.values("H", offset)[0]

    def string(self, offset, scrambled=False, *, allow_zero=False):
        if offset == 0 and not allow_zero:
            return ""
        self.span(offset, 1)
        end = self.data.find(b"\0", offset, min(offset + 4096, len(self.data)))
        if end < 0:
            raise ValueError("旧版资源名称未结束")
        value = self.data[offset:end]
        if scrambled and value and not 0x20 <= value[0] < 0xA0:
            value = bytes((v + 128) & 255 for v in value)
        try:
            return value.decode("utf8")
        except UnicodeDecodeError:
            return value.decode("shift_jis", "replace")


def read_file(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("旧版资源超过 256 MiB 的读取上限")
    return path.read_bytes()


def xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def named_key(name, size):
    return xor(xor(KEY_A[:size], KEY_B[:size]), name.encode("shift_jis").ljust(size, b"\0")[:size])


def decrypt_cbc_cts(data, name, size=16):
    """First 16 bytes are the IV; partial tails use CBC ciphertext stealing.

    Complete blocks use ordinary CBC, including the final two blocks. 19 CN
    uses a 128-bit key derived from the filename including its extension;
    17 uses a 256-bit key derived from the graphics directory name.
    """
    from Crypto.Cipher import AES
    if not 32 <= len(data) <= MAX_BYTES:
        raise ValueError("加密资源长度无效")
    key = named_key(name, size)
    iv, cipher = data[:16], data[16:]
    tail = len(cipher) % 16
    if not tail:
        return AES.new(key, AES.MODE_CBC, iv).decrypt(cipher)
    at = len(cipher) - 16 - tail
    prefix = AES.new(key, AES.MODE_CBC, iv).decrypt(cipher[:at]) if at else b""
    ecb = AES.new(key, AES.MODE_ECB)
    block = ecb.decrypt(cipher[at:at + 16])
    stolen = cipher[at + 16:]
    before = xor(ecb.decrypt(stolen + block[tail:]), cipher[at - 16:at] if at else iv)
    return prefix + before + xor(block[:tail], stolen)


def txp2_bytes(path, data):
    if data[:4] in {b"TXP2", b"2PXT"}:
        return data
    if len(data) < 40:
        return None
    # Probe one block before decrypting a potentially unrelated .bin file.
    from Crypto.Cipher import AES
    try:
        key = named_key(Path(path).name, 16)
    except UnicodeEncodeError:
        return None
    head = xor(AES.new(key, AES.MODE_ECB).decrypt(data[16:32]), data[:16])
    if head[:4] not in {b"TXP2", b"2PXT"}:
        return None
    return decrypt_cbc_cts(data, Path(path).name)


def inflate(data, expected=None, *, avs=False, limit=None):
    """Two distinct 4 KiB LZ dialects; cap allocation before decoding.

    Legacy streams use absolute ring positions, AVS uses backward distances.
    A limit permits cheap TDXT header inspection without inflating the atlas.
    """
    if expected is None:
        if len(data) < 4:
            raise ValueError("LZSS 长度头被截断")
        expected = struct.unpack_from("<I", data)[0]
        data = data[4:]
    if not 0 < expected <= MAX_BYTES or expected > len(data) * 9 + 18:
        raise ValueError("LZ 解压长度无效或超过上限")
    wanted = min(expected, limit) if limit is not None else expected
    out = bytearray(4096)
    cursor, ring, flags = 0, 4078, 0
    while len(out) - 4096 < wanted:
        flags >>= 1
        if not flags & 256:
            if cursor >= len(data):
                raise ValueError("LZ 控制字被截断")
            flags = data[cursor] | 0xFF00
            cursor += 1
        if flags & 1:
            if cursor >= len(data):
                raise ValueError("LZ 字节被截断")
            out.append(data[cursor])
            cursor += 1
            ring = (ring + 1) & 4095
            continue
        if cursor + 2 > len(data):
            raise ValueError("LZ 回溯引用被截断")
        a, b = data[cursor:cursor + 2]
        cursor += 2
        count = (b & 15) + 3
        if avs:
            distance = ((a << 8) | b) >> 4
            if distance == 0:
                raise ValueError("LZ 流提前结束")
            offset = -distance
        else:
            offset = (a | ((b & 240) << 4)) - ring
            if offset >= 0:
                offset -= 4096
        amount = min(count, 4096 + wanted - len(out))
        start = len(out) + offset
        if amount <= -offset:
            out.extend(out[start:start + amount])
        else:
            for _ in range(amount):
                out.append(out[len(out) + offset])
        ring = (ring + count) & 4095
    return bytes(out[4096:])


def texture_blob(data, flags, limit=None):
    if data[:4] in {b"TDXT", b"TXDT"}:
        return data if limit is None else data[:limit]
    r = BinaryReader(data, ">")
    expected, coded = r.values("II", 0)
    if not 64 <= expected <= MAX_BYTES:
        raise ValueError("TDXT 解压长度无效")
    payload = r.span(8, coded or expected)
    if not coded or not flags & (4 | 0x40000):
        if len(payload) != expected:
            raise ValueError("未压缩 TDXT 长度不匹配")
        return payload if limit is None else payload[:limit]
    return inflate(payload, expected, avs=bool(flags & 0x40000), limit=limit)


def dimensions(width, height):
    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > MAX_PIXELS:
        raise ValueError("旧版纹理尺寸无效或超过上限")
    return width, height


def tdxt_header(data):
    if data[:4] not in {b"TDXT", b"TXDT"} or len(data) < 64:
        raise ValueError("TDXT 纹理头无效")
    r = BinaryReader(data, "<" if data[:4] == b"TDXT" else ">")
    width, height, flags = r.values("HHI", 16)
    dimensions(width, height)
    return width, height, flags & 255


def pixels16(raw, size, fmt):
    count = size[0] * size[1]
    if len(raw) < count * 2:
        raise ValueError("16 位纹理像素被截断")
    import numpy as np
    values=np.frombuffer(raw,dtype='<u2',count=count)
    output=np.empty((count,4),np.uint8)
    for channel,shift in enumerate((8,4,0,12) if fmt==0x1F else (10,5,0)):
        output[:,channel]=((values>>shift)&15)*17 if fmt==0x1F else (((values>>shift)&31)*255+15)//31
    if fmt!=0x1F:output[:,3]=(values>>15)*255
    return Image.frombytes("RGBA", size, output.tobytes())


def tdxt_image(data):
    width, height, fmt = tdxt_header(data)
    size, raw = (width, height), data[64:]
    if fmt in {0x10, 0x20}:
        return Image.frombytes("RGBA", size, raw, "raw", "BGRA")
    if fmt == 0x15:
        return Image.frombytes("RGBA", size, raw, "raw", "ARGB")
    if fmt == 0x0E:
        return Image.frombytes("RGB", size, raw).convert("RGBA")
    if fmt == 0x01:
        return Image.frombytes("L", size, raw).convert("RGBA")
    if fmt == 0x0B:
        return Image.frombytes("RGB", size, raw, "raw", "BGR;16").convert("RGBA")
    if fmt in {0x13, 0x1F}:
        return pixels16(raw, size, fmt)
    if fmt in {0x16, 0x17, 0x18, 0x19, 0x1A}:
        fourcc = b"DXT1" if fmt == 0x16 else b"DXT3" if fmt in {0x17, 0x18} else b"DXT5"
        header = struct.pack("<7I", 124, 0x1007, height, width, 0, 0, 0) + bytes(44)
        header += struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
        return Image.open(BytesIO(b"DDS " + header + raw)).convert("RGBA")
    raise ValueError(f"暂不支持 TDXT 像素格式 0x{fmt:02x}")


def gc_image(data, stem):
    try:
        plain = inflate(data)
        if plain[:3] != b"GC ":
            raise ValueError("解压结果不是 GC 纹理")
    except ValueError:
        from Crypto.Cipher import Blowfish
        if len(data) % 8:
            raise ValueError("GCZ 压缩流或加密长度无效")
        iv = b"tukupypy"
        seed = stem.encode("shift_jis").ljust(24, b"\0")[:24]
        derived = Blowfish.new(b"omE872M2akmRkERh4177S", Blowfish.MODE_CBC, iv).encrypt(seed)
        key = (derived[:-1] + b"\0").split(b"\0")[0]
        plain = inflate(Blowfish.new(key, Blowfish.MODE_CBC, iv).decrypt(data))
    r = BinaryReader(plain, ">")
    if r.span(0, 3) != b"GC ":
        raise ValueError("GC 纹理标识无效")
    x, y, width, height = r.values("4H", 8)
    size = dimensions(width, height)
    depth = r.span(19, 1)[0]
    if depth == 8:
        raise ValueError("暂不支持 GC 调色板纹理")
    count = width * height * (4 if depth == 32 else 2)
    if r.u32(20) != count:
        raise ValueError("GC 像素长度与尺寸不匹配")
    raw = r.span(24, count)
    image = Image.frombytes("RGBA", size, raw, "raw", "BGRA") if depth == 32 else pixels16(raw, size, 0x13)
    return image, (x, y)
