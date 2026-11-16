from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from iidxviewer.afp import Animation, Movie, Timeline
from iidxviewer.animation_inspection import describe, game_usage, text_report
from iidxviewer.resources import Package
from iidxviewer.service import ResourceService


def fixture():
    child = Timeline([(0,1)], [(127,dict(flags=2,depth=3,character=2))])
    root = Timeline([(0,1)], [(127,dict(flags=2,depth=7,character=1,clip=9))],[(0,'in')])
    movie = Movie('title',root,(0,64,0,32),60,{1:('sprite',child),2:('image','pixel')},[(1,'logo')])
    return Package(Path('title.ifs')),Animation('title',movie,root)


class AnimationInspectionTests(unittest.TestCase):
    def test_nested_depths_keep_parent_identity_and_image_source(self):
        package, animation = fixture()
        report = describe(package,animation,package.path,None)
        row = report['layers'][0]
        self.assertEqual((row['depth'],row['exports'],row['mask_end']),(7,['logo'],9))
        self.assertEqual(row['children'][0]['path'],[7,3])
        self.assertEqual(row['children'][0]['source'],'tex/pixel.png')
        self.assertEqual(report['labels'],[dict(frame=0,name='in')])

    def test_exported_sprite_has_distinct_entry_and_authored_frames(self):
        package, animation = fixture()
        child = Animation('title / logo',animation.movie,animation.movie.definitions[1][1])
        report = describe(package,child,package.path,None)
        self.assertEqual((report['entry'],report['character'],report['source_afp']),('sprite',1,'afp/title'))

    def test_same_filename_in_another_directory_is_not_usage_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'bm2dx.dll').write_bytes(b'test')
            engine=dict(library=dict(path=str(root/'afp-core.dll')))
            index=dict(path=str(root/'bm2dx.dll'),sha256='test',strings=[(0x1234,'/0/title.ifs')])
            with patch('iidxviewer.animation_inspection.dll_strings',return_value=index):
                wrong=game_usage(root/'data/graphic/1/title.ifs',engine,'title')
                correct=game_usage(root/'data/graphic/0/title.ifs',engine,'title')
            self.assertEqual(wrong['purpose'],'未识别')
            self.assertEqual(len(wrong['candidates']),1)
            self.assertEqual(correct['purpose'],'标题画面')
            self.assertEqual(correct['references'][0]['rva'],'0x1234')

    def test_names_without_a_dll_reference_are_not_scene_labels(self):
        package, animation = fixture()
        report=describe(package,animation,package.path,None,dll_error='unsupported')
        self.assertEqual(report['game_usage']['purpose'],'未识别')
        self.assertIn('DLL 读取：unsupported',text_report(report))

    def test_cycle_is_bounded(self):
        package, animation = fixture()
        animation.movie.definitions[2]=('sprite',animation.timeline)
        report=describe(package,animation,package.path,None)
        self.assertEqual(report['layers'][0]['children'][0]['children'],[])

    def test_legacy_index_structure_preserves_group_and_cell_reference(self):
        animation=SimpleNamespace(legacy=True,name='scene',start=0,length=10,cell_names={0:'cell_0'},
            index=SimpleNamespace(records=[dict(type=2,id=2,begin=0,end=10),dict(type=-1),
                                           dict(type=0,id=0,begin=1,end=9),dict(type=-1)]))
        report=describe(Package(Path('owl.bin')),animation,Path('owl.bin'),None)
        self.assertEqual(report['entry'],'legacy')
        self.assertEqual(report['layers'][0]['children'][0]['source'],'tex/cell_0.png')
        self.assertIn('索引记录 0',text_report(report))

    def test_dll_reading_is_explicit_and_cached_per_package(self):
        package, animation = fixture();package.animations=[animation]
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder)
            service.accept_package(package);service.original_path=package.path
            service.engine=dict(library=dict(path='afp-core.dll'))
            reply=dict(ok=True,library_sha256='hash',animations=[dict(name='afp/title',frames=1,fps=60,width=64,height=32)])
            try:
                with patch('iidxviewer.dll_inspection.inspect_package',return_value=reply) as probe:
                    service.inspect_animation(0,False);probe.assert_not_called()
                    first=service.inspect_animation(0);service.inspect_animation(0)
                    self.assertEqual(probe.call_count,1)
                    self.assertIn('DLL 读取根时间轴',first['text'])
                    service.accept_package(package);service.inspect_animation(0)
                    self.assertEqual(probe.call_count,2)
            finally:service.close()
