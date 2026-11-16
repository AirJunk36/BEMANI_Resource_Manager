from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from iidxviewer.musicdb import annotate,catalog,database,entry,metadata,related_songs,song_id,song_key,visual_info
from iidxviewer.resources import CatalogEntry


def legacy_db(root,encrypted=False):
    mid=1523;n=2098 if encrypted else 1900;stride=828;start=16+n*2
    data=bytearray(start+stride);struct.pack_into('<4sIHHI',data,0,b'IIDX',20 if encrypted else 18,1,n,0)
    for i in range(n):struct.pack_into('<H',data,16+i*2,0xffff)
    struct.pack_into('<H',data,16+mid*2,0);data[start:start+6]=b'ICARUS'
    struct.pack_into('<II',data,start+420,mid,117);data[start+428:start+436]=b'00000000'
    struct.pack_into('<h',data,start+436,-30);data[start+440:start+448]=b'military'
    path=root/'info'/('music_data.bin' if encrypted else 'eout.bin');path.parent.mkdir(parents=True)
    if encrypted:
        from Crypto.Cipher import AES
        from iidxviewer.legacy_codecs import named_key
        iv=bytes(range(16));assert len(data)%16==0
        data=iv+AES.new(named_key('music_data',16),AES.MODE_CBC,iv).encrypt(bytes(data))
    path.write_bytes(data);return path


class SongIdTests(unittest.TestCase):
    def test_four_digit_ids_use_style_and_song_without_colliding_with_modern_ids(self):
        for path,raw,key in [('graph/sys/0301/owl.bin',301,3001),('graphic/1523.bin',1523,15023),
                             ('sd_data/1523/1523a.2dx',1523,15023),('movie/0200.4',200,2000),
                             ('graphic/02005.ifs',2005,2005),('sound/1523_pre.2dx',1523,15023)]:
            self.assertEqual(song_id(path),raw,path);self.assertEqual(song_key(path),key,path)
        self.assertIsNone(song_id('graphic/title.ifs'))

    def test_old_tables_plain_and_encrypted_provide_search_and_asset_matching(self):
        for encrypted in [False,True]:
            with self.subTest(encrypted=encrypted),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve()/'game/data';db=legacy_db(root,encrypted);before=db.read_bytes()
                audio=root/'sd_data/1523/1523.2dx';audio.parent.mkdir(parents=True);audio.touch()
                layer=root/'graph_data/sys/1523.bin';layer.parent.mkdir(parents=True);layer.touch()
                video=root/'movie/military.4';video.parent.mkdir();video.touch()
                row=metadata(audio);self.assertEqual(row['title'],'ICARUS');self.assertEqual(row['id_text'],'1523')
                self.assertEqual(catalog(root)[15023]['title'],'ICARUS')
                self.assertEqual(entry(db,1523)['bga'],'military');self.assertIsNone(entry(db,-1))
                files=[CatalogEntry(p,str(p.relative_to(root)),'game','动画/图像资源',0) for p in [audio,layer]]
                self.assertTrue(all('ICARUS' in title and '1523' in title and '15023' in title for _,title in annotate(files)))
                visuals=visual_info(audio);self.assertEqual(visuals['layer_path'],str(layer));self.assertEqual(visuals['bga_path'],str(video))
                self.assertEqual(related_songs(layer)[0]['path'],str(audio));self.assertEqual(related_songs(video)[0]['path'],str(audio))
                self.assertEqual(db.read_bytes(),before)

    def test_earlier_names_reuse_only_titles_from_sibling_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()/'12_test/data';path=root/'graph/sys/2000/owl.bin';path.parent.mkdir(parents=True);path.touch()
            with patch('iidxviewer.musicdb.nearby_catalog',return_value={20000:dict(title='Legacy name',title_ascii='',bga='newer',version=33)}):
                title=list(annotate([CatalogEntry(path,'graph/sys/2000/owl.bin','12_test','动画/图像资源',0)]))[0][1]
                self.assertIn('Legacy name',title);self.assertIn('2000',title)
                self.assertIsNone(metadata(path));self.assertIsNone(visual_info(path)['bga_path'])

    def test_unknown_layout_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=legacy_db(Path(folder));data=bytearray(path.read_bytes());data.append(0);path.write_bytes(data)
            with self.assertRaises(ValueError):database(str(path),len(data),path.stat().st_mtime_ns)
