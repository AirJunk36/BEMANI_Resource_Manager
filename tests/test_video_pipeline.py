"""Buffered encoding must keep every pixel/frame and stop on a broken pipe."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from PySide6.QtGui import QGuiApplication,QColor,QImage
from iidxviewer.media_export import encode,frames_for
from tests.test_media_export import ffmpeg


class Drawing:
    animation=SimpleNamespace(length=20,movie=SimpleNamespace(fps=60,rect=(0,16,0,8)))
    def __init__(self):self.image=QImage(16,8,QImage.Format.Format_RGBA8888);self.count=0
    def render(self,frame,size):
        self.count+=1;self.image.fill(QColor(frame*11,250-frame*7,frame*5,30+frame*9));return self.image


class VideoPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_threaded_stream_keeps_rgba_order_timing_and_reused_image_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'lossless.mkv'
            expected=b''.join(frames_for(Drawing(),20/60,(16,8),60,stretch=True))
            frames=frames_for(Drawing(),20/60,(16,8),60,stretch=True,buffers=True)
            encode(['-f','rawvideo','-pix_fmt','rgba','-s','16x8','-r','60','-i','pipe:0','-c:v','ffv1','-pix_fmt','bgra',str(target)],frames)
            actual=ffmpeg('-i',target,'-f','rawvideo','-pix_fmt','rgba','pipe:1')
            self.assertEqual(actual,expected)

    def test_encoder_failure_stops_the_producer(self):
        drawing=Drawing()
        with tempfile.TemporaryDirectory() as directory,self.assertRaises((OSError,ValueError)):
            encode(['-f','rawvideo','-pix_fmt','rgba','-s','16x8','-r','60','-i','pipe:0','-c:v','missing_encoder',str(Path(directory)/'bad.mp4')],
                   frames_for(drawing,100,(16,8),60,loop=True,stretch=True,buffers=True))
        self.assertLess(drawing.count,6000)
