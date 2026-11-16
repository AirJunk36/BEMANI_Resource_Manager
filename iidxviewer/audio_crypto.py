"""Legacy 2DX scrambling and 19 CN filename AES; transform copies only.

Format references: djhackersdev/legacy-tools, iidx/old 2dx tools/crack2dx.c;
SaxxonPike/scharfrichter, Scharfrichter/Archives/Bemani2DX.cs.
The bulk decoder uses native array operations rather than a Python block loop.
"""
import struct

KEYS = {
    b'%eNc': bytes.fromhex('971e24a09a00102b91efd77acd11afaf8d265dbbe0c61b2b'),
    b'%e10': bytes.fromhex('2d865662d7fdcaa4b32460262481dbc257b1746fa7529921'),
    b'%e11': bytes.fromhex('edf09c90441a5a03ab07c199232432c75f32a597ad980f8f'),
}
KEYS[b'%e12'] = KEYS[b'%hid'] = KEYS[b'%e11']
PARTIAL = {b'%e12', b'%hid'}


def named_2dx(data, name):
    """Probe two AES blocks before allocating a decoded bank."""
    from pathlib import PurePosixPath
    if not name or PurePosixPath(name).suffix.lower() != '.2dx' or not 88 <= len(data) <= 256 * 1024 * 1024:
        return False
    def header_valid(head, size):
        start, count = struct.unpack_from('<II', head, 16)
        return 0 < count <= 100000 and 0x48 + count * 4 <= start <= size
    if header_valid(data, len(data)):
        return False
    from Crypto.Cipher import AES
    from .legacy_codecs import named_key
    try:
        key = named_key(PurePosixPath(name).name, 16)
    except UnicodeEncodeError:
        return False
    head = AES.new(key, AES.MODE_CBC, data[:16]).decrypt(data[16:48])
    return header_valid(head, len(data) - 16)


def decode_2dx(data, name=None):
    if named_2dx(data, name):
        from pathlib import PurePosixPath
        from .legacy_codecs import decrypt_cbc_cts
        return decrypt_cbc_cts(data, PurePosixPath(name).name)
    tag = data[:4]
    if tag not in KEYS:
        if tag.startswith(b'%'):
            raise ValueError('暂不支持此 2DX 加密类型')
        return data
    if len(data) < 8:
        raise ValueError('2DX 加密头被截断')
    length = struct.unpack_from('<I', data, 4)[0]
    padded = (length + 7) & ~7
    if length < 0x48 or padded > len(data) - 8 or padded > 256 * 1024 * 1024:
        raise ValueError('2DX 加密数据长度无效')
    import numpy as np
    encoded = np.frombuffer(data, dtype=np.uint8, count=padded, offset=8).reshape(-1, 8)
    key = np.frombuffer(KEYS[tag], dtype=np.uint8).reshape(3, 8)
    def mix(block):
        a = block[:, 0] * 63 + block[:, 3]
        d = block[:, 1] * 17 + block[:, 2]
        e = a + d
        g = e * block[:, 3] + a + 51
        block[:, 4] ^= a ^ d
        block[:, 5] ^= d
        block[:, 6] ^= g ^ e
        block[:, 7] ^= g
    block = encoded ^ key[0]
    mix(block)
    block = np.concatenate((block[:, 4:], block[:, :4]), axis=1)
    if tag not in PARTIAL:
        block ^= key[1]
        mix(block)
        block ^= key[2]
    block[1:] ^= encoded[:-1]
    return block.tobytes()[:length]


def encode_2dx(plain, original, name=None):
    """Preserve the bank's scrambling type when saving a replaced track."""
    if named_2dx(original, name):
        from pathlib import PurePosixPath
        from .packing import encrypt_cbc_cts
        return encrypt_cbc_cts(plain, PurePosixPath(name).name)
    tag = original[:4]
    if tag not in KEYS:
        return plain
    keys = struct.unpack('<3Q', KEYS[tag])
    def mix(word):
        a = ((word & 255) * 63 + ((word >> 24) & 255)) & 255
        d = (((word >> 8) & 255) * 17 + ((word >> 16) & 255)) & 255
        e = (a + d) & 255
        g = (e * ((word >> 24) & 255) + a + 51) & 255
        return word ^ ((a ^ d) << 32 | d << 40 | (g ^ e) << 48 | g << 56)
    padded = plain + bytes((-len(plain)) % 8)
    result = bytearray(len(padded))
    previous = 0
    for at in range(0, len(padded), 8):
        word = struct.unpack_from('<Q', padded, at)[0] ^ previous
        if tag not in PARTIAL:
            word = mix(word ^ keys[2]) ^ keys[1]
        word = (word >> 32) | ((word & 0xffffffff) << 32)
        previous = mix(word) ^ keys[0]
        struct.pack_into('<Q', result, at, previous)
    old_length = struct.unpack_from('<I', original, 4)[0]
    tail = original[8 + ((old_length + 7) & ~7):]
    return tag + struct.pack('<I', len(plain)) + result + tail
