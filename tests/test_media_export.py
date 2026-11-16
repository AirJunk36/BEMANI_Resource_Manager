from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import struct
import subprocess
import tempfile
import unittest

import imageio_ffmpeg
import numpy as np
from PIL import Image
from PySide6.QtGui import QGuiApplication, QImage, QColor

from iidxviewer.media_export import animation_mp4, song_movie, video_mp4
from iidxviewer.musicdb import entry, visual_info, annotate, related_songs
from iidxviewer.resources import Asset, Package, CatalogEntry
from iidxviewer.service import ResourceService
from iidxviewer.song import song_info
from tests.test_song import pcm, chart_file
from tests.test_replacements import two_tracks


def ffmpeg(*args):
    return subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error',*map(str,args)],capture_output=True,check=True).stdout


def pixel(path, at=0):
    data=ffmpeg('-ss',at,'-i',path,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1')
    return np.frombuffer(data,dtype=np.uint8).reshape(48,64,3)[24,32].astype(int)


def db_fixture(root, version=20):
    wide=version>=32; mid=20032; n=mid+1; stride=2040 if wide else 800
    data=bytearray(16+n*(4 if wide else 2)+stride)
    struct.pack_into('<4sIHHI',data,0,b'IIDX',version,1,0 if wide else n,n if wide else 0)
    for i in range(n):struct.pack_into('<I' if wide else '<H',data,16+i*(4 if wide else 2),0xffffffff if wide else 0xffff)
    struct.pack_into('<I' if wide else '<H',data,16+mid*(4 if wide else 2),0)
    at=16+n*(4 if wide else 2);title='Ascalon'.encode('utf-16le' if wide else 'cp932');data[at:at+len(title)]=title
    artist='Qrispy Joybox'.encode('utf-16le' if wide else 'cp932');artist_at=at+(448 if wide else 192);data[artist_at:artist_at+len(artist)]=artist
    pos=at+(1660 if wide else 456);struct.pack_into('<II',data,pos,mid,95)
    ids=b'0000000000' if wide else b'00000000';data[pos+8:pos+8+len(ids)]=ids
    delay=pos+8+len(ids);struct.pack_into('<h',data,delay,-30)
    movie=delay+(2 if wide else 4);data[movie:movie+4]=b'19po';struct.pack_into('<I',data,movie+32,2)
    data[movie+36+32:movie+36+35]=b'seq'
    path=root/'info/music_data.bin';path.parent.mkdir(parents=True);path.write_bytes(data)
    return path


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_database_generic_bga_layer_offset_and_title(self):
        for version in [20,34]:
            with self.subTest(version=version),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)/'game/data';db=db_fixture(root,version)
                row=entry(db,20032);self.assertEqual(row['title'],'Ascalon');self.assertEqual(row['artist'],'Qrispy Joybox');self.assertEqual(row['bga'],'19po');self.assertEqual(row['bga_offset'],-.5)
                (root/'movie').mkdir();(root/'movie/19po.mp4').touch();(root/'graphic').mkdir();(root/'graphic/20032.ifs').touch()
                result=visual_info(root/'sound/20032.ifs')
                self.assertTrue(result['bga_path'].endswith('19po.mp4'));self.assertEqual(result['layer_animation'],'seq')
                files=[CatalogEntry(root/'sound/20032.ifs','sound/20032.ifs','game','音频类文件',0),CatalogEntry(root/'movie/19po.mp4','movie/19po.mp4','game','BGA',0)]
                self.assertTrue(all('Ascalon' in title for _,title in annotate(files)))
                self.assertTrue(all('Qrispy Joybox' in title for _,title in annotate(files)))
                from iidxviewer.musicdb import song_labels
                self.assertEqual(song_labels(root/'sound/20032.ifs'),dict(song_title='Ascalon',song_artist='Qrispy Joybox'))
                (root/'sound').mkdir();(root/'sound/20032.ifs').touch()
                self.assertEqual(related_songs(root/'movie/19po.mp4')[0]['id'],'20032')
                self.assertEqual(related_songs(root/'graphic/20032.ifs')[0]['id'],'20032')

    def test_animation_mp4_preserves_source_time_and_alpha(self):
        class Drawing:
            animation=SimpleNamespace(length=3,movie=SimpleNamespace(fps=30,rect=(0,64,0,48)))
            frames=[]
            def render(self,frame,size):
                self.frames.append(frame);image=QImage(*size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(255,0,0,128));return image
        with tempfile.TemporaryDirectory() as folder,patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
            root=Path(folder);drawing=Drawing();target=root/'animation.mp4'
            animation_mp4(drawing,target,root,60,'480p')
            self.assertEqual(drawing.frames,[0,1,2]);self.assertLess(abs(pixel(target)[0]-128),30)
            raw=ffmpeg('-i',target,'-f','rawvideo','-pix_fmt','rgb24','pipe:1')
            self.assertEqual(len(raw),64*48*3*6)
            before=target.read_bytes()
            with self.assertRaises(FileExistsError):animation_mp4(drawing,target,root,60,'480p')
            self.assertEqual(target.read_bytes(),before)

    def test_song_movie_alpha_offset_and_non_mp4_video(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
            root=Path(folder);audio=root/'song.wav';audio.write_bytes(pcm(2000,44100//2))
            source=root/'bga.avi';ffmpeg('-f','lavfi','-i','color=c=blue:s=64x48:r=60:d=0.5','-c:v','mpeg4',source)
            layer=root/'layer.png';Image.new('RGBA',(64,48),(255,0,0,128)).save(layer)
            target=root/'song.mp4';song_movie(audio,target,root,dict(bga_path=str(source),layer_path=str(layer),layer_animation=0,
                layer_offset=.25,resolution='480p',fps=60,bga_loop=False))
            self.assertGreater(pixel(target,.05)[2],180)
            last=pixel(target,.35);self.assertLess(abs(last[0]-last[2]),45);self.assertGreater(last[0],70)
            converted=root/'converted.mp4';video_mp4(Asset('bga.avi','视频',source),converted,root)
            self.assertGreater(pixel(converted)[2],180)

    def test_song_previews_reuse_cache_and_remain_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);song=root/'20_test/data/sound/20032/20032.2dx';song.parent.mkdir(parents=True)
            song.write_bytes(two_tracks(pcm(1000)));song.with_suffix('.1').write_bytes(chart_file([(0,4,1,120),(0,7,0,1)]))
            cache=root/'cache';cache.mkdir();service=ResourceService(cache)
            try:
                service.open(song);args=dict(method='prepare_song',chart=1,bank='package')
                first=service.dispatch(args);second=service.dispatch(args)
                self.assertEqual(first['path'],second['path'])
                with self.assertRaises(ValueError):service.dispatch(dict(method='export_song',chart=1,bank='package',target=str(song.parent/'bad.wav')))
                out=root/'good.wav';service.dispatch(dict(method='export_song',chart=1,bank='package',target=str(out)))
                self.assertEqual(out.read_bytes(),Path(first['path']).read_bytes())
                self.assertEqual(len(service.song_previews),1)
            finally:service.close()
