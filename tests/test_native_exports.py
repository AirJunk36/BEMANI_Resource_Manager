"""Native material dimensions, fixed animation bounds, and old VFS manifests."""
from io import BytesIO
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.resources import Asset,Package,open_package
from iidxviewer.render import SceneRenderer
from iidxviewer.service import ResourceService
from iidxviewer.media_export import video_size
from iidxviewer.editing import expand_tracks
from iidxviewer.exporting import native_animation
from tests.test_replacements import two_tracks
from tests.test_song import pcm,chart_file

ROOT=Path(os.environ.get('IIDX_GAME_ROOT',Path(__file__).resolve().parents[2]))


def manifest(path,names):
    path.write_bytes(b'IIDX'+struct.pack('<III',16,len(names),2)+b''.join(n.encode().ljust(32,b'\0')+bytes(20) for n in names))


def moving_package(path):
    image=BytesIO();Image.new('RGBA',(11,13),'red').save(image,'PNG')
    asset=Asset('tex/sprite.png','图片',payload=image.getvalue())
    animation=SimpleNamespace(name='main',legacy=True,length=3,movie=SimpleNamespace(rect=(0,640,0,480),fps=30,warnings=set()),
        draw_commands=lambda frame,hidden: [('sprite',('sprite',(11,13),(1,0,0,1,100+4*frame,50),1,'normal'))])
    return Package(path,assets=[asset],files={asset.name:asset},animations=[animation])


class NativeExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_image_preview_uses_pixels_without_an_intermediate_png_roundtrip(self):
        class Source:
            def image(self):return Image.new('RGBA',(31,27),(37,82,156,129))
            def load(self):raise AssertionError('Preview must not encode an intermediate PNG')
        with tempfile.TemporaryDirectory() as directory:
            service=ResourceService(Path(directory));service.accept_package(Package(Path('material.bin'),assets=[Asset('sprite.png','图片',Source())]))
            try:
                result=service.asset(0)
                with Image.open(result['path']) as image:
                    self.assertEqual(image.size,(31,27));self.assertEqual(image.getpixel((0,0)),(37,82,156,129))
                self.assertIs(service.asset(0),result)
            finally:service.close()

    def test_texture_memory_is_bounded_and_recent_textures_survive(self):
        class Pixels:
            width=height=4096
            def convert(self,mode):return self
        class Source:
            def image(self):return Pixels()
        package=Package(Path('material.bin'),files={f'tex/{i}.png':Asset(str(i),'图片',Source()) for i in range(4)})
        package.texture('0');package.texture('1');package.texture('0');package.texture('2')
        self.assertEqual(set(package._image_cache),{'0','2'});self.assertEqual(package._image_bytes,128*1024**2)
        package.close();self.assertEqual(package._image_bytes,0);self.assertFalse(package._image_cache)

    def test_gpu_and_cpu_keep_the_same_cropped_pixel_coordinates(self):
        from iidxviewer.gpu import GpuDevice,GpuSceneRenderer,GpuUnavailable
        try:device=GpuDevice()
        except GpuUnavailable as error:self.skipTest(str(error))
        package=moving_package(Path('material.bin'));cpu=SceneRenderer(package,package.animations[0]);gpu=GpuSceneRenderer(package,package.animations[0],device)
        try:
            for drawing in [cpu,gpu]:
                with native_animation(drawing):
                    self.assertEqual(drawing.rect,(100,119,50,63))
                    image=drawing.render(2)
                    self.assertEqual((image.width(),image.height()),(19,13))
                    self.assertEqual(image.pixelColor(8,0).getRgb(),(255,0,0,255))
                    self.assertEqual(image.pixelColor(7,0).alpha(),0)
                self.assertEqual(drawing.rect,(0,640,0,480))
        finally:cpu.close();gpu.close();device.close();package.close()

    def test_frame_is_trimmed_and_moving_exports_keep_one_native_rectangle(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cache=root/'cache';cache.mkdir();service=ResourceService(cache)
            service.accept_package(moving_package(root/'material.bin'))
            try:
                for kind,name in [('frame','frame.png'),('sequence','frames'),('webp','animation.webp'),('gif','animation.gif'),('mp4','animation.mp4')]:
                    target=root/name
                    service.dispatch(dict(method='export',kind=kind,animation=0,frame=2,target=str(target),renderer='cpu',resolution='1080p'))
                    if kind=='frame':self.assertEqual(Image.open(target).size,(11,13))
                    elif kind=='sequence':
                        frames=[Image.open(p).convert('RGBA') for p in sorted(target.glob('*.png'))]
                        self.assertEqual([im.size for im in frames],[(19,13)]*3)
                        self.assertEqual([im.getbbox() for im in frames],[(0,0,11,13),(4,0,15,13),(8,0,19,13)])
                    elif kind=='mp4':
                        stat=target.stat();self.assertEqual(video_size(str(target),stat.st_size,stat.st_mtime_ns),(19,13))
                    else:self.assertEqual(Image.open(target).size,(19,13))
                self.assertEqual(service.package.animations[0].movie.rect,(0,640,0,480))
                self.assertEqual((service.render(0,0,renderer='cpu')['kind']),'image')
            finally:service.close()

    def test_osprey_audio_and_movie_entries_are_usable_and_lazy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game=root/'1409';game.mkdir();cache=root/'cache';cache.mkdir()
            bank=two_tracks(pcm(1000,441));(game/'1409.2dx').write_bytes(bank)
            (game/'1409.1').write_bytes(chart_file([(0,7,0,1),(300,6,0,0)]));((game/'1409.4').resolve()).write_bytes(b'movie')
            path=game/'osprey.bin';manifest(path,['1409.1','1409.2dx','1409.4'])
            original=path.read_bytes();service=ResourceService(cache)
            try:
                info=service.open(path);self.assertTrue(info['song_preview'])
                parent=next(a for a in service.package.assets if a.kind=='音频包')
                self.assertIsInstance(parent.source,Path);self.assertIsNone(parent.payload)
                first=expand_tracks(service.package,parent);self.assertIs(first,expand_tracks(service.package,parent))
                self.assertEqual(len(first),2);self.assertTrue(all(a.edit_target is None for a in first))
                self.assertEqual(next(a for a in service.package.assets if a.kind=='视频').source,(game/'1409.4').resolve())
                target=root/'audio';service.dispatch(dict(method='export',kind='audio',target=str(target)))
                self.assertEqual(len(list(target.glob('*.wav'))),2)
                self.assertEqual((game/'1409.2dx').read_bytes(),bank);self.assertEqual(path.read_bytes(),original)
            finally:service.close()

    def test_real_vanessa_uses_movie_window_and_exports_the_sprite_size(self):
        paths=[ROOT/'16_EMPRESS/data/graph/sys/1409/osprey.bin',ROOT/'18_Resort_Anthem/data/graph_data/sys/1409.bin']
        if not all(p.is_file() for p in paths):self.skipTest('VANESSA fixtures unavailable')
        for path in paths:
            with self.subTest(path=path),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);cache=root/'cache';cache.mkdir();service=ResourceService(cache)
                try:
                    service.open(path);drawing=SceneRenderer(service.package,service.package.animations[0])
                    try:
                        frame=round(drawing.animation.movie.fps)
                        self.assertEqual(drawing.rect,(168,472,0,416));image=drawing.render(frame)
                        self.assertEqual((image.width(),image.height()),(304,416))
                        self.assertGreater(image.pixelColor(102,1).alpha(),0)
                    finally:drawing.close()
                    target=root/'frame.png';service.dispatch(dict(method='export',kind='frame',animation=0,frame=frame,target=str(target),renderer='cpu'))
                    self.assertEqual(Image.open(target).size,(100,180))
                    if service.package.format=='legacy':
                        local=next(i for i,a in enumerate(service.package.animations) if a.name=='K_')
                        info=service.render(local,30,renderer='cpu')
                        with Image.open(info['path']) as image:
                            self.assertEqual(image.size,(100,180));self.assertIsNotNone(image.getbbox())
                finally:service.close()
