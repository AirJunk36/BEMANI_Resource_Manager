"""Encrypted banks must preview, preserve other tracks and never change the source."""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
from iidxviewer.audio_crypto import KEYS, decode_2dx, encode_2dx
from iidxviewer.replacement_media import MediaConverter, replace_audio_track
from iidxviewer.resources import audio_assets
from iidxviewer.service import ResourceService
from tests.test_replacements import tone, two_tracks

ROOT = Path(os.environ.get('IIDX_GAME_ROOT', Path(__file__).resolve().parents[2]))
SAMPLES = {
    b'%eNc': '0101/01011.2dx', b'%e10': '0110/0110.2dx',
    b'%e11': '0109/0109.2dx', b'%e12': '0100/01001.2dx',
}


class LegacyAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_real_schemes_decode_to_pcm_without_source_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ResourceService(folder)
            try:
                for tag, relative in SAMPLES.items():
                    with self.subTest(tag=tag):
                        source = ROOT / '12_HAPPY_SKY/data/sd_data' / relative
                        if not source.exists():
                            self.skipTest('Missing legacy audio fixture: ' + relative)
                        raw = source.read_bytes(); before = hashlib.sha256(raw).digest()
                        self.assertEqual(raw[:4], tag)
                        plain = decode_2dx(raw)
                        self.assertEqual(encode_2dx(plain, raw), raw)
                        info = service.open(source)
                        self.assertGreater(len(info['assets']), 1)
                        preview = service.asset(0)
                        self.assertEqual(preview['kind'], 'audio')
                        with wave.open(preview['path']) as wav:
                            self.assertGreater(wav.getnframes(), 0)
                            self.assertGreater(wav.getframerate(), 0)
                        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            finally:
                service.close()

    def test_replace_track_preserves_scrambling_metadata_and_other_track(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            original = two_tracks(tone(folder / 'old.wav'))
            tone(folder / 'new.wav', frequency=880)
            converter = MediaConverter(folder)
            for tag in KEYS:
                with self.subTest(tag=tag):
                    encoded = encode_2dx(original, tag + struct.pack('<I', 0))
                    changed = replace_audio_track(encoded, 0, folder / 'new.wav', converter)
                    self.assertEqual(changed[:4], tag)
                    plain = decode_2dx(changed)
                    before = audio_assets(original, 'bank')
                    after = audio_assets(changed, 'bank')
                    self.assertEqual(len(after), 2)
                    self.assertNotEqual(after[0].read(), before[0].read())
                    self.assertEqual(after[1].read(), before[1].read())
                    self.assertEqual(plain[24:72], original[24:72])

    def test_invalid_scrambled_banks_fail_before_allocating(self):
        for data in [b'%e12', b'%e12' + struct.pack('<I', 0),
                     b'%e12' + struct.pack('<I', 0xffffffff),
                     b'%eNc' + struct.pack('<I', 80) + bytes(79), b'%bad' + bytes(100)]:
            with self.subTest(data=data[:8]), self.assertRaises(ValueError):
                audio_assets(data, 'bad.2dx')
