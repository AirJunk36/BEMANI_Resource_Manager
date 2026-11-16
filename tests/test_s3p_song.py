from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest
import wave

import numpy as np

from iidxviewer.resources import Asset, Package, open_package
from iidxviewer.song import export_song, song_info
from iidxviewer.musicdb import related_songs
from iidxviewer.service import ResourceService
from tests.test_song import pcm, chart_file
from tests.test_media_export import db_fixture


def s3p(*sounds):
    data=bytearray(8+len(sounds)*8)
    struct.pack_into('<4sI',data,0,b'S3P0',len(sounds))
    for i,sound in enumerate(sounds):
        payload=struct.pack('<4sII20x',b'S3V0',32,len(sound))+sound
        struct.pack_into('<II',data,8+i*8,len(data),len(payload));data.extend(payload)
    return bytes(data)


class S3PSongTests(unittest.TestCase):
    def test_main_bank_exposes_virtual_preview_without_adding_an_audio_track(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);main=root/'30000.s3p';voice=root/'voice.s3p'
            main.write_bytes(s3p(pcm(1000)));voice.write_bytes(main.read_bytes())
            service=ResourceService(root/'cache')
            try:
                info=service.open(main)
                self.assertTrue(info['song_preview']);self.assertEqual(len(info['assets']),1)
                self.assertFalse(service.open(voice)['song_preview'])
                self.assertEqual(main.read_bytes(),voice.read_bytes())
            finally:service.close()

    def test_bgm_and_key_sounds_are_mixed_from_matching_chart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'33_test/data/sound/30000/30000.s3p';source.parent.mkdir(parents=True)
            source.write_bytes(s3p(pcm(4000,8820),pcm(8000)))
            before=source.read_bytes();source.with_suffix('.1').write_bytes(chart_file([(0,4,1,120),(0,7,0,1),(0,2,0,2),(50,0,0,0),(250,6,0,0)]))
            package=open_package(source)
            try:
                info=song_info(package,source)
                self.assertFalse(info['needs_chart']);self.assertEqual(info['bank'],'package');self.assertEqual(info['charts'][0]['id'],1)
                output=root/'complete.wav';result=export_song(package,source,output,root,None,1)
                with wave.open(str(output)) as wav:audio=np.frombuffer(wav.readframes(wav.getnframes()),'<i2').reshape(-1,2)
                self.assertAlmostEqual(result['seconds'],.25,places=4)
                self.assertEqual(result['samples'],2);self.assertEqual(audio[100,0],4000);self.assertEqual(audio[3000,0],12000);self.assertEqual(audio[8000,0],4000)
                self.assertEqual(source.read_bytes(),before)
            finally:package.close()

    def test_ifs_contained_s3p_uses_full_bank_and_internal_chart(self):
        bank=s3p(pcm(1000),pcm(2000));chart=chart_file([(0,4,1,120),(0,7,0,1),(0,2,0,2),(50,0,0,0)])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=Package(root/'30000.ifs',assets=[Asset('sound/30000.s3p','音频包',payload=bank),Asset('sound/30000.1','文件',payload=chart)])
            info=song_info(package,'33_test/data/sound/30000.ifs')
            self.assertEqual(info['bank'],'0');self.assertFalse(info['needs_chart'])
            result=export_song(package,'33_test/data/sound/30000.ifs',root/'internal.wav',root,None,1)
            self.assertEqual(result['samples'],2)

    def test_bga_reverse_lookup_finds_s3p_ahead_of_preview_bank(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'game/data';db_fixture(root,34)
            movie=root/'movie/19po.mp4';movie.parent.mkdir();movie.touch()
            bank=root/'sound/20032/20032.s3p';bank.parent.mkdir(parents=True);bank.touch();bank.with_name('20032_pre.2dx').touch()
            result=related_songs(movie)
            self.assertEqual(len(result),1);self.assertEqual(Path(result[0]['path']),bank.resolve())
            self.assertEqual(related_songs(bank)[0]['path'],str(bank.resolve()))

    def test_s3p_without_chart_still_reports_need_for_chart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'voice.s3p';path.write_bytes(s3p(pcm(1000)))
            package=open_package(path)
            try:self.assertTrue(song_info(package,path)['needs_chart'])
            finally:package.close()
