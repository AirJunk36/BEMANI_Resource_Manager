from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from iidxviewer.resources import Asset,scan
from iidxviewer.song import decode_sample
from iidxviewer.song_decode import decode_samples
from tests.test_song import pcm
from tests.test_media_export import ffmpeg


class LoadingTests(unittest.TestCase):
    def test_batched_keysounds_match_single_decode_and_cache_survives_new_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);blobs=[]
            for i in range(2):
                source=root/f'{i}.wav';source.write_bytes(pcm(1000+i*1000,4410));encoded=root/f'{i}-adpcm.wav'
                ffmpeg('-i',source,'-c:a','adpcm_ms',encoded);blobs.append(encoded.read_bytes())
            baseline=root/'baseline';baseline.mkdir()
            expected=[decode_sample(blob,baseline,i)[0].read_bytes() for i,blob in enumerate(blobs)]
            sounds=[Asset(str(i),'音频',payload=blobs[i%2]) for i in range(26)]
            with patch('iidxviewer.song_decode.ROOT',root/'shared'):
                first=root/'first';first.mkdir();values=decode_samples(sounds,range(1,27),first)
                self.assertEqual(set(values),set(range(1,27)))
                for sample,(path,count) in values.items():
                    self.assertEqual(path.read_bytes(),expected[(sample-1)%2]);self.assertEqual(count,len(expected[(sample-1)%2])//8)
                second=root/'second';second.mkdir()
                with patch('iidxviewer.song_decode.subprocess.run',side_effect=AssertionError('Cached samples must not start a decoder')):
                    reused=decode_samples(sounds,range(1,27),second)
                self.assertEqual({i:p.read_bytes() for i,(p,_) in reused.items()},{i:p.read_bytes() for i,(p,_) in values.items()})

    def test_scanning_preserves_versions_order_and_reloads_metadata_without_following_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'20_parent';game=root/'33_child/data';game.mkdir(parents=True)
            for name in ['title10.ifs','title2.ifs','model.moc3','model.model3.json']:(game/name).write_bytes(b'asset')
            (root/'.hidden').mkdir();(root/'.hidden/hidden.ifs').touch()
            outside=root.parent/'outside';outside.mkdir();(outside/'other.ifs').touch()
            try:(game/'linked').symlink_to(outside,target_is_directory=True)
            except OSError:pass
            files=scan(root)
            self.assertEqual([p.path.name for p in files],['model.model3.json','title2.ifs','title10.ifs'])
            self.assertEqual({p.version for p in files},{'33_child'})
            (game/'title2.ifs').write_bytes(b'changed-size')
            self.assertEqual(next(p.size for p in scan(root) if p.path.name=='title2.ifs'),12)

    def test_english_catalog_preserves_placeholders_and_has_no_untranslated_values(self):
        import re
        path=Path(__file__).resolve().parents[1]/'iidxviewer/locales/en.json';values=json.loads(path.read_text(encoding='utf-8'))
        self.assertGreater(len(values),300)
        for source,english in values.items():
            self.assertEqual(set(re.findall(r'\{\d+\}',source)),set(re.findall(r'\{\d+\}',english)),source)
            self.assertIsNone(re.search('[\u4e00-\u9fff]',english),source)
        for key in ['资源库','完整乐曲预览','专业模式','导出 MP4…','动画/图像资源']:self.assertIn(key,values)
