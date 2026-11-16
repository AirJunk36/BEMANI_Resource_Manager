"""Read-only SSP banks: sparse SD9 offsets at 0x48, including 19 CN AES.

The table index is the sound ID, not the ordinal of a nonempty entry.
Plain banks retain only their index; encrypted banks share one decoded buffer.
"""
from dataclasses import dataclass
from pathlib import Path
import struct

from .legacy_codecs import MAX_BYTES, decrypt_cbc_cts, named_key, xor


@dataclass(frozen=True)
class SSPTrack:
    archive: object
    sound_id: int
    offset: int
    sd9_size: int
    wave_offset: int
    wave_size: int

    def load(self):
        return self.archive.read(self.wave_offset, self.wave_size)


class SSPArchive:
    def __init__(self, path, cancelled=lambda: False):
        self.path = Path(path)
        self.stream = self.path.open('rb')
        self.data = None
        self.closed = False
        try:
            self.size = self.path.stat().st_size
            if not 0x48 <= self.size <= MAX_BYTES:
                raise ValueError('SSP 长度无效或超过 256 MiB')
            head = self.read(0, 0x48)
            self.encrypted = not self.is_header(head)
            if self.encrypted:
                from Crypto.Cipher import AES
                key = named_key(self.path.name, 16)
                probe = xor(AES.new(key, AES.MODE_ECB).decrypt(head[16:32]), head[:16])
                if not self.is_header(probe):
                    raise ValueError('无法识别此 SSP 或加密方式')
                self.data = decrypt_cbc_cts(self.read(0, self.size), self.path.name)
                self.size = len(self.data)
                self.stream.close()
                head = self.read(0, 0x48)
            start, count = struct.unpack_from('<II', head, 0x10)
            slots = (start - 0x48) // 4
            if start > self.size or (start - 0x48) % 4 or not 0 < count <= slots <= 100000:
                raise ValueError('SSP 索引无效')
            table = struct.unpack(f'<{slots}I', self.read(0x48, slots * 4))
            offsets = [(sound_id, off) for sound_id, off in enumerate(table) if off]
            if len(offsets) != count:
                raise ValueError('SSP 音频数量与索引不符')
            self.tracks = []
            end = start
            for sound_id, off in sorted(offsets, key=lambda item: item[1]):
                if cancelled():
                    raise InterruptedError()
                if off < end:
                    raise ValueError('SSP 音频范围重叠')
                record = self.read(off, 32)
                if record[:4] != b'SD9\0':
                    raise ValueError(f'SSP 编号 {sound_id} 缺少 SD9 头')
                header, size = struct.unpack_from('<II', record, 4)
                if header < 32 or size < 12 or off + header + size > self.size:
                    raise ValueError(f'SSP 编号 {sound_id} 的 SD9 范围无效')
                wave = self.read(off + header, 12)
                wave_size = struct.unpack_from('<I', wave, 4)[0] + 8
                if wave[:4] != b'RIFF' or wave[8:12] != b'WAVE' or not 12 <= wave_size <= size:
                    raise ValueError(f'SSP 编号 {sound_id} 的 WAV 无效')
                self.tracks.append(SSPTrack(self, sound_id, off, header + size, off + header, wave_size))
                end = off + header + size
            self.tracks.sort(key=lambda track: track.sound_id)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def is_header(head):
        return head[:16].split(b'\0', 1)[0].lower().endswith(b'.ssp')

    def read(self, offset, size):
        if self.closed:
            raise ValueError('SSP 已关闭')
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError('SSP 数据被截断')
        if self.data is not None:
            return self.data[offset:offset + size]
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError('SSP 数据被截断')
        return data

    def close(self):
        self.closed = True
        self.stream.close()
        self.data = None

    def manifest(self):
        return {'format': 'SSP', 'source': self.path.name, 'encrypted': self.encrypted,
                'tracks': [{'id': t.sound_id, 'name': f'{self.path.stem}_{t.sound_id:05d}.wav',
                            'offset': t.offset, 'sd9_size': t.sd9_size, 'wave_size': t.wave_size}
                           for t in self.tracks]}
