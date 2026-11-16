"""19 CN AES banks/charts must unpack, preview and edit without touching originals."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import wave

from PySide6.QtGui import QGuiApplication
from iidxviewer.audio_crypto import decode_2dx, named_2dx
from iidxviewer.packing import encrypt_cbc_cts
from iidxviewer.resources import audio_assets
from iidxviewer.service import ResourceService
from iidxviewer.song import decode_chart, charts
from tests.test_replacements import tone, two_tracks
from tests.test_song import chart_file

ROOT=Path(os.environ.get('IIDX_GAME_ROOT',Path(__file__).resolve().parents[2]))


class CNAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_aes_bank_full_and_partial_blocks_reject_wrong_names(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);plain=two_tracks(tone(folder/'tone.wav'))
            for tail in [0,7]:
                data=plain+bytes((-len(plain))%16+tail)
                encrypted=encrypt_cbc_cts(data,'song.2dx')
                self.assertTrue(named_2dx(encrypted,'song.2dx'))
                self.assertEqual(decode_2dx(encrypted,'song.2dx'),data)
                self.assertEqual(len(audio_assets(encrypted,'song.2dx')),2)
                with self.assertRaises(ValueError):audio_assets(encrypted,'wrong.2dx')
                self.assertIs(decode_2dx(data,'song.2dx'),data)

    def test_replace_save_and_whole_bank_preserve_aes_filename_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);game=folder/'19_demo';game.mkdir();cache=folder/'cache';cache.mkdir()
            plain=two_tracks(tone(folder/'old.wav'));source=game/'original.2dx'
            original=encrypt_cbc_cts(plain,source.name);source.write_bytes(original)
            replacement=folder/'different.2dx';replacement.write_bytes(encrypt_cbc_cts(plain,replacement.name))
            service=ResourceService(cache)
            try:
                service.open(source)
                replacement_wave=folder/'new.wav';tone(replacement_wave,frequency=880)
                service.dispatch(dict(method='replace',scope='asset',asset=0,source=str(replacement_wave)))
                changed=service.package.path.read_bytes()
                self.assertTrue(named_2dx(changed,source.name))
                before,after=audio_assets(original,source.name),audio_assets(changed,source.name)
                self.assertNotEqual(before[0].read(),after[0].read());self.assertEqual(before[1].read(),after[1].read())
                saved=service.dispatch(dict(method='save_replacement',target=str(folder/'saved')))
                self.assertEqual(audio_assets(Path(saved['primary']).read_bytes(),source.name)[1].read(),before[1].read())
                service.dispatch(dict(method='replace',scope='file',source=str(replacement)))
                self.assertEqual(decode_2dx(service.package.path.read_bytes(),source.name),plain)
                self.assertEqual(source.read_bytes(),original)
            finally:service.close()

    def test_aes_chart_matches_plain_chart_and_wrong_names_remain_invalid(self):
        plain=chart_file([(100,0,0,1),(500,6,0,0)])
        encrypted=encrypt_cbc_cts(plain,'song.1')
        self.assertEqual(decode_chart(encrypted,'song.1'),plain)
        with self.assertRaises(ValueError):charts(decode_chart(encrypted,'wrong.1'))

    def test_real_cn_preview_chart_and_every_unpacked_track_match_source(self):
        sound=ROOT/'19_Lincle_cn/contents/modules/data/sound'
        samples=['0202/02022.2dx','0202/0202_pre.2dx','0100/01001.2dx','sys_sd/sys_ending.2dx']
        if not (sound/samples[0]).exists():self.skipTest('No 19 CN audio fixtures')
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);cache=folder/'cache';cache.mkdir();service=ResourceService(cache)
            try:
                for index,relative in enumerate(samples):
                    path=sound/relative;before=hashlib.sha256(path.read_bytes()).digest();service.open(path)
                    expected=audio_assets(decode_2dx(path.read_bytes(),path.name),path.name)
                    preview=service.asset(0)
                    with wave.open(preview['path']) as wav:self.assertGreater(wav.getnframes(),0)
                    target=folder/f'unpacked-{index}'
                    service.dispatch(dict(method='export',kind='audio',target=str(target)))
                    self.assertEqual(len(list(target.iterdir())),len(expected))
                    for track in expected:self.assertEqual((target/track.name).read_bytes(),track.read())
                    if index==0:
                        info=service.dispatch(dict(method='song_info'))
                        self.assertEqual([c['id'] for c in info['charts']],[0,1,6,7])
                    self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)
            finally:service.close()
