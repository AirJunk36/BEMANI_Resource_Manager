"""SSP sparse IDs, encrypted tails, bounded parsing and real audio exports."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))
ROOT = Path(os.environ.get('IIDX_GAME_ROOT', HOME.parent))

from iidxviewer.packing import encrypt_cbc_cts
from iidxviewer.resources import Asset, Package, open_package, scan
from iidxviewer.service import ResourceService


def bank():
    audio = BytesIO()
    with wave.open(audio, 'wb') as out:
        out.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
        out.writeframes(struct.pack('<4h', 0, 1000, -1000, 0))
    wav = audio.getvalue()
    slots = 81
    data = bytearray(0x48 + slots * 4)
    data[:16] = b'sys_sound.ssp\0\0\0'
    struct.pack_into('<II', data, 0x10, len(data), 2)
    for sound_id in (0, 80):
        struct.pack_into('<I', data, 0x48 + sound_id * 4, len(data))
        data += b'SD9\0' + struct.pack('<II', 32, len(wav)) + bytes(20) + wav
    return bytes(data), wav


class SSPTests(unittest.TestCase):
    def test_unpack_duplicate_names_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            service = ResourceService(folder)
            service.package = Package(folder / 'bank.2dx', assets=[
                Asset(name, '音频', payload=payload) for name, payload in
                [('a.wav', b'one'), ('00003_a.wav', b'two'), ('a.wav', b'three')]])
            try:
                target = folder / 'unpacked'
                service.dispatch({'method': 'export', 'kind': 'audio', 'target': str(target)})
                self.assertEqual(sorted(p.read_bytes() for p in target.iterdir()), [b'one', b'three', b'two'])
            finally:
                service.close()

    def test_sparse_ids_and_lazy_plain_bank(self):
        data, wav = bank()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sys_sound.ssp'
            path.write_bytes(data)
            self.assertEqual(scan(Path(folder))[0].kind, '音频类文件')
            package = open_package(path)
            archive = package.archive
            try:
                self.assertEqual([t.sound_id for t in archive.tracks], [0, 80])
                self.assertEqual([a.name for a in package.assets], ['sys_sound_00000.wav', 'sys_sound_00080.wav'])
                self.assertIsNone(archive.data)
                self.assertTrue(all(a.payload is None and a.edit_target is None for a in package.assets))
                self.assertTrue(all(a.read() == wav for a in package.assets))
            finally:
                package.close()
            self.assertTrue(archive.stream.closed)
            with self.assertRaises(ValueError):
                package.assets[0].read()

    def test_encrypted_bank_complete_and_partial_aes_blocks(self):
        data, wav = bank()
        for tail in range(16):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'sys_sound.ssp'
                # Trailing alignment bytes are outside all indexed records.
                path.write_bytes(encrypt_cbc_cts(data + bytes(tail), path.name))
                package = open_package(path)
                try:
                    self.assertTrue(package.archive.encrypted)
                    self.assertEqual(package.assets[-1].read(), wav)
                finally:
                    package.close()
                self.assertIsNone(package.archive.data)

    def test_bad_indices_and_wave_ranges_are_rejected(self):
        data, _ = bank()
        bad = [data[:20], data[:-1], bytes(len(data))]
        for at, value in [(0x10, 0xffffffff), (0x14, 3), (0x48, 4), (0x48 + 80 * 4, len(data) + 1)]:
            changed = bytearray(data)
            struct.pack_into('<I', changed, at, value)
            bad.append(changed)
        first = struct.unpack_from('<I', data, 0x48)[0]
        for at, value in [(first + 4, 12), (first + 8, 0xffffffff), (first + 36, 0xffffffff)]:
            changed = bytearray(data)
            struct.pack_into('<I', changed, at, value)
            bad.append(changed)
        for number, blob in enumerate(bad):
            with self.subTest(number=number), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'bad.ssp'
                path.write_bytes(blob)
                with self.assertRaises(ValueError):
                    open_package(path)

    def test_service_preview_unpack_and_protect_source(self):
        data, wav = bank()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            game = folder / 'game'; game.mkdir()
            cache = folder / 'cache'; cache.mkdir()
            source = game / 'sys_sound.ssp'; source.write_bytes(data)
            service = ResourceService(cache)
            try:
                info = service.open(source)
                self.assertEqual(len(info['assets']), 2)
                self.assertFalse(info['assets'][0]['replaceable'])
                preview = service.asset(1)
                self.assertEqual(preview['kind'], 'audio')
                with wave.open(preview['path']) as sound:
                    self.assertEqual(sound.getnframes(), 4)
                target = folder / 'unpacked'
                service.dispatch({'method': 'export', 'kind': 'audio', 'target': str(target)})
                self.assertEqual((target / 'sys_sound_00080.wav').read_bytes(), wav)
                index = json.loads((target / 'index.json').read_text())
                self.assertEqual([t['id'] for t in index['tracks']], [0, 80])
                with self.assertRaises(FileExistsError):
                    service.dispatch({'method': 'export', 'kind': 'audio', 'target': str(target)})
                with self.assertRaises(ValueError):
                    service.dispatch({'method': 'export', 'kind': 'audio', 'target': str(game / 'unpacked')})
                self.assertEqual(source.read_bytes(), data)
            finally:
                service.close()


class RealSSPTests(unittest.TestCase):
    def test_actual_sparse_banks_and_original_hashes(self):
        expected = {'16_EMPRESS': 60, '18_Resort_Anthem': 325, '19_Lincle_cn': 293}
        found = 0
        for version, count in expected.items():
            source = next((ROOT / version).rglob('sys_sound.ssp'), None)
            if source is None:
                continue
            found += 1
            with self.subTest(version=version):
                before = hashlib.sha256(source.read_bytes()).digest()
                package = open_package(source)
                try:
                    self.assertEqual(len(package.assets), count)
                    for asset in package.assets:
                        data = asset.read()
                        self.assertEqual(data[:4], b'RIFF')
                        self.assertEqual(data[8:12], b'WAVE')
                        self.assertEqual(len(data), struct.unpack_from('<I', data, 4)[0] + 8)
                finally:
                    package.close()
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
        if not found:
            self.skipTest('本地没有 SSP 实样')
