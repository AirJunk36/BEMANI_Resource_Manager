from io import BytesIO
from pathlib import Path
import gc
import struct
import tempfile
import unittest
import wave
import weakref
from unittest.mock import patch

import numpy as np

from iidxviewer.resources import Asset, Package, open_package
from iidxviewer.service import ResourceService
from iidxviewer.song import export_song, song_info, triggers
from tests.test_playback import fixture, timeline, place


def pcm(value, frames=4410):
    data = BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        wav.writeframes(np.full((frames, 2), value, dtype='<i2').tobytes())
    return data.getvalue()


def chart_file(events):
    payload = b''.join(struct.pack('<iBBH', *e) for e in events + [(0x7fffffff, 0, 0, 0)])
    header = bytearray(96)
    struct.pack_into('<II', header, 8, 96, len(payload))
    return bytes(header) + payload


class SongTests(unittest.TestCase):
    def test_key_assignment_sides_autoplay_and_charge_note(self):
        data = chart_file([(0,4,1,120),(0,12,0,0),(0,2,0,1),(0,3,0,2),(0,7,0,3),
                           (100,0,0,100),(200,1,0,0),(300,2,0,3),(400,0,0,0),(2000,12,0,0)])
        result, _, clock = triggers(data, 1, Path('20_tricoro/song.2dx'))
        self.assertEqual(result, [(0,3),(.1,1),(.2,2),(.4,3)])
        self.assertEqual(clock, 1000)
        self.assertEqual(triggers(data, 1, Path('song.2dx'))[2], 1000)

    def test_legacy_clock(self):
        data = chart_file([(0,4,1,120),(0,12,0,0),(60,7,0,1),(120,12,0,0)])
        self.assertAlmostEqual(triggers(data, 1, '12_HAPPY_SKY/song.2dx')[0][0][0], 60/59.94)
        self.assertEqual(triggers(data, 1, '14_GOLD/song.2dx')[2], 60.05)
        self.assertEqual(triggers(data, 1, 'song.2dx')[2], 59.94)

    def test_multi_spin_scratch_uses_scratch_lane_sound(self):
        data=chart_file([(0,2,7,1),(100,0,107,100),(200,0,107,100)])
        self.assertEqual(triggers(data,1,'33_test/song.2dx')[0],[(.1,1),(.2,1)])

    def test_mixed_samples_have_correct_positions_overlap_and_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); package = Package(root/'song.2dx', assets=[Asset('a.wav','音频',payload=pcm(4000)), Asset('b.wav','音频',payload=pcm(8000))])
            data = chart_file([(0,4,1,120),(0,12,0,0),(0,2,0,1),(0,7,0,1),(50,0,0,0),(100,7,0,2),(2000,12,0,0)])
            chart = root/'song.1'; chart.write_bytes(data)
            target = root/'export.wav'
            export_song(package, '20_tricoro/song.2dx', target, root, None, 1, str(chart))
            with wave.open(str(target),'rb') as wav:
                self.assertEqual(wav.getframerate(),44100)
                samples = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,2)
            self.assertEqual(len(samples),8820)
            self.assertEqual(samples[100,0],4000)
            self.assertEqual(samples[3000,0],8000)
            self.assertEqual(samples[5000,0],12000)
            self.assertEqual(samples[8000,0],8000)
            original=target.read_bytes()
            with self.assertRaises(FileExistsError):
                export_song(package, '20_tricoro/song.2dx', target, root, None, 1, str(chart))
            self.assertEqual(target.read_bytes(),original)
            self.assertFalse(list(root.glob('song-*')))

    def test_missing_samples_do_not_export_partial_song(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); chart=root/'song.1'; chart.write_bytes(chart_file([(0,7,0,99)]))
            package=Package(root/'song.2dx',assets=[Asset('a','音频',payload=pcm(100))])
            with self.assertRaisesRegex(ValueError,'缺少第 99'):
                export_song(package,'20_test/song.2dx',root/'bad.wav',root,None,1,str(chart))
            self.assertFalse((root/'bad.wav').exists())

    def test_malformed_chart_and_missing_assignment(self):
        for data in [bytes(95), struct.pack('<II',96,1000)+bytes(88), chart_file([(0,0,0,0)])]:
            with self.assertRaises(ValueError): triggers(data,1,'20_test/song.2dx')

    def test_renderer_switch_releases_packages_without_waiting_for_gc(self):
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder)
            package, animation=fixture(timeline([[place(2)]]))
            package.animations=[animation]
            service.accept_package(package)
            renderer=service.new_renderer(animation,'cpu')
            service.renderers[0,'cpu']=renderer
            reference=weakref.ref(package)
            was_enabled=gc.isenabled();gc.disable()
            try:
                del package, animation, renderer
                with patch('iidxviewer.service.gc.collect', return_value=0):
                    service.accept_package(Package(Path('next.bin')))
                self.assertIsNone(reference())
            finally:
                if was_enabled: gc.enable()
                service.close()

    def test_media_preview_is_reused_and_files_are_bounded(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); data=BytesIO();Image.new('RGBA',(2,2)).save(data,'PNG')
            service=ResourceService(root)
            service.accept_package(Package(root/'images.ifs',assets=[Asset(str(i),'图片',payload=data.getvalue()) for i in range(25)]))
            first=service.asset(0);self.assertEqual(service.asset(0),first)
            for i in range(1,25):service.asset(i)
            self.assertLessEqual(len(list(root.glob('asset_*'))),8)
            service.accept_package(Package(root/'next.bin'))
            self.assertFalse(list(root.glob('asset_*')))
            service.close()
