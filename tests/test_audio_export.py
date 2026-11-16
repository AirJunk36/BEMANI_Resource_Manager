"""Original audio exports preserve encoded bytes, independently of preview PCM."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))
ROOT = Path(os.environ.get('IIDX_GAME_ROOT', HOME.parent))

from iidxviewer.exporting import original_audio
from iidxviewer.resources import Asset, audio_assets, media_suffix
from iidxviewer.service import ResourceService

ASF = bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c')
AUDIO = bytes.fromhex('409e69f84d5bcf11a8fd00805f5c442b')
VIDEO = bytes.fromhex('c0ef19bc4d5bcf11a8fd00805f5c442b')


def wrapped(data, offset=32):
    return b'S3V0' + struct.pack('<III', offset, len(data), 0) + bytes(offset - 16) + data


class AudioExportTests(unittest.TestCase):
    def test_asf_audio_and_video_have_correct_extensions(self):
        self.assertEqual(media_suffix(ASF + AUDIO), '.wma')
        self.assertEqual(media_suffix(ASF + VIDEO + AUDIO), '.wmv')
        self.assertEqual(media_suffix(ASF), '.wmv')
        with self.assertRaises(ValueError):
            original_audio(Asset('movie.s3v', '音频', payload=ASF + VIDEO + AUDIO))

    def test_s3p_track_name_and_payload_preserve_original_encoding(self):
        wma = ASF + AUDIO + b'encoded audio'
        entry = wrapped(wma)
        bank = b'S3P0' + struct.pack('<III', 1, 16, len(entry)) + entry
        track, = audio_assets(bank, 'voice.s3p')
        self.assertEqual(track.name, 'voice_0001.wma')
        self.assertEqual(track.read(), wma)

    def test_raw_and_wrapped_s3v_export_without_transcoding(self):
        wma = ASF + AUDIO + b'encoded audio'
        for blob in [wma, wrapped(wma), wrapped(wma, 70000)]:
            with self.subTest(bytes=len(blob)), tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                game = folder / 'game'; game.mkdir()
                cache = folder / 'cache'; cache.mkdir()
                source = game / 'voice.s3v'; source.write_bytes(blob)
                service = ResourceService(cache)
                try:
                    service.open(source)
                    with patch.object(service.package.assets[0], 'read', side_effect=AssertionError('Probe must not read the full asset')):
                        info = service.dispatch({'method': 'audio_export_info', 'asset': 0})
                    self.assertEqual(info['name'], 'voice.wma')
                    output = folder / info['name']
                    with patch('subprocess.run', side_effect=AssertionError('Export must not transcode')):
                        service.dispatch({'method': 'export', 'kind': 'audio_original', 'asset': 0, 'target': str(output)})
                    self.assertEqual(output.read_bytes(), wma)
                    unpacked = folder / 'unpacked'
                    service.dispatch({'method': 'export', 'kind': 'audio', 'target': str(unpacked)})
                    self.assertEqual((unpacked / 'voice.wma').read_bytes(), wma)
                    raw = folder / 'original.s3v'
                    service.dispatch({'method': 'export', 'kind': 'asset', 'asset': 0, 'target': str(raw)})
                    self.assertEqual(raw.read_bytes(), blob)
                    with self.assertRaises(FileExistsError):
                        service.dispatch({'method': 'export', 'kind': 'audio_original', 'asset': 0, 'target': str(output)})
                    with self.assertRaises(ValueError):
                        service.dispatch({'method': 'export', 'kind': 'audio_original', 'asset': 0, 'target': str(game / 'voice.wma')})
                    self.assertEqual(source.read_bytes(), blob)
                finally:
                    service.close()

    def test_invalid_wrapped_audio_and_non_audio_are_rejected(self):
        for blob in [wrapped(ASF + AUDIO)[:24], b'S3V0' + struct.pack('<I', 0xffffffff) + bytes(40)]:
            with self.assertRaises(ValueError):
                original_audio(Asset('bad.s3v', '音频', payload=blob))
        with self.assertRaises(ValueError):
            original_audio(Asset('image.png', '图片', payload=b'png'))


class RealOriginalAudioTests(unittest.TestCase):
    def test_actual_s3v_and_s3p_preview_then_original_export(self):
        import imageio_ffmpeg
        files = list((ROOT / '33_Sparkle_Shower/data/sound/stamp').rglob('*.s3v'))[:3]
        bank = ROOT / '33_Sparkle_Shower/data/sound/voice/vo034_miku/vo034_06_tutorial.s3p'
        if bank.exists():
            files.append(bank)
        if not files:
            self.skipTest('本地没有 S3V/S3P 实样')
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            service = ResourceService(folder)
            try:
                for number, source in enumerate(files):
                    with self.subTest(source=source.name):
                        before = hashlib.sha256(source.read_bytes()).digest()
                        service.open(source)
                        self.assertEqual(service.asset(0)['kind'], 'audio')
                        info = service.dispatch({'method': 'audio_export_info', 'asset': 0})
                        self.assertTrue(info['name'].endswith('.wma'))
                        target = folder / f'{number}.wma'
                        service.dispatch({'method': 'export', 'kind': 'audio_original', 'asset': 0, 'target': str(target)})
                        _, encoded = original_audio(service.package.assets[0])
                        self.assertEqual(target.read_bytes(), encoded)
                        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-nostdin', '-i', str(target), '-f', 'null', '-'], capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            finally:
                service.close()
