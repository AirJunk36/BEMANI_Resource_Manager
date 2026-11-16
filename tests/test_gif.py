"""GIF exports preserve motion, timing, transparency and existing files."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtGui import QGuiApplication,QColor,QImage,QPainter
from iidxviewer.media_export import animation_gif
from iidxviewer.service import ResourceService
from tests.test_media_export import ffmpeg
from tests.test_replacements import two_tracks
from tests.test_song import pcm,chart_file


class GIFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_animation_gif_preserves_motion_transparency_duration_and_loop(self):
        class Drawing:
            animation=SimpleNamespace(length=30,movie=SimpleNamespace(fps=60,rect=(0,64,0,48)))
            def render(self,frame,size):
                image=QImage(*size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
                painter=QPainter(image);painter.fillRect(frame,10,8,8,QColor('red'));painter.end();return image
        with tempfile.TemporaryDirectory() as directory,patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
            folder=Path(directory);target=folder/'animation.gif'
            animation_gif(Drawing(),target,folder,120,'480p')
            with Image.open(target) as image:
                self.assertEqual(image.format,'GIF');self.assertEqual(image.info['loop'],0)
                self.assertGreater(image.n_frames,10)
                self.assertEqual(image.convert('RGBA').getpixel((0,0))[3],0)
                first=image.convert('RGBA').tobytes();duration=0
                for index in range(image.n_frames):image.seek(index);duration+=image.info['duration']
                self.assertNotEqual(first,image.convert('RGBA').tobytes())
                self.assertAlmostEqual(duration,500,delta=20)
            before=target.read_bytes()
            with self.assertRaises(FileExistsError):animation_gif(Drawing(),target,folder,60,'480p')
            self.assertEqual(target.read_bytes(),before)

    def test_video_and_song_visual_gif_exports_use_the_public_service(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
            folder=Path(directory);cache=folder/'cache';cache.mkdir();game=folder/'20_demo';game.mkdir()
            video=game/'visual.mp4';ffmpeg('-f','lavfi','-i','testsrc=size=64x48:rate=20:duration=0.3','-pix_fmt','yuv420p',video)
            before=hashlib.sha256(video.read_bytes()).digest();service=ResourceService(cache)
            try:
                service.open(video);target=folder/'video.gif'
                service.dispatch(dict(method='export',kind='gif',asset=0,target=str(target),resolution='480p'))
                with Image.open(target) as image:self.assertGreater(image.n_frames,1)
                audio=game/'song.2dx';audio.write_bytes(two_tracks(pcm(1000,4410)))
                audio.with_suffix('.1').write_bytes(chart_file([(0,7,0,1),(300,6,0,0)]))
                service.open(audio);target=folder/'song.gif'
                service.dispatch(dict(method='export_song_gif',bank='package',chart=1,target=str(target),options=dict(bga_path=str(video),resolution='480p',renderer='cpu')))
                with Image.open(target) as image:self.assertGreater(image.n_frames,1)
                self.assertEqual(hashlib.sha256(video.read_bytes()).digest(),before)
            finally:service.close()
