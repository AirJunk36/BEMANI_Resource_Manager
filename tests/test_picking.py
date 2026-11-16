import math
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.afp import Animation, Movie
from iidxviewer.layers import LayerControls
from iidxviewer.picking import pick
from iidxviewer.resources import Package
from iidxviewer.scene import Scene
from iidxviewer.service import ResourceService
from tests.test_playback import timeline, place

ROOT=Path(os.environ.get('IIDX_GAME_ROOT',Path(__file__).resolve().parents[2]))

def scene_for(placements, images, size=16):
    root=timeline([placements])
    movie=Movie('pick',root,(0,size,0,size),60,{i:('image',name) for i,name in enumerate(images,1)},[])
    animation=Animation('pick',movie,root)
    package=Package(Path('pick.ifs'),animations=[animation]);package.texture=images.get
    scene=Scene(package,animation);scene.controls=LayerControls(package,animation)
    return scene

class PickingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_topmost_and_transparent_holes(self):
        front=Image.new('RGBA',(16,16),'blue');front.paste((0,0,0,0),(0,0,8,16))
        scene=scene_for([place(1,1),place(2,2)],{'back':Image.new('RGBA',(16,16),'red'),'front':front})
        self.assertEqual(pick(scene,0,.25,.5)[0],'root/1:1')
        key,bounds=pick(scene,0,.75,.5)
        self.assertEqual(key,'root/2:2');self.assertEqual(bounds,[0,0,1,1])

    def test_hidden_and_zero_opacity_layers_are_not_pickable(self):
        scene=scene_for([place(1,1),place(2,2)],{'a':Image.new('RGBA',(16,16),'red'),'b':Image.new('RGBA',(16,16),'blue')})
        scene.controls.load();scene.controls.set_visible('root/2:2',False)
        self.assertEqual(pick(scene,0,.5,.5)[0],'root/1:1')
        scene.controls.set_visible('root',False)
        self.assertEqual(pick(scene,0,.5,.5),(None,None))
        scene=scene_for([place(1,1,multiply=(1,1,1,0))],{'a':Image.new('RGBA',(16,16),'red')})
        self.assertIsNone(pick(scene,0,.5,.5)[0])

    def test_masks_exclude_clipped_pixels(self):
        mask=Image.new('RGBA',(16,16));mask.paste('white',(0,0,8,16))
        scene=scene_for([place(1,1,clip=2),place(2,2)],{'mask':mask,'a':Image.new('RGBA',(16,16),'red')})
        self.assertEqual(pick(scene,0,.25,.5)[0],'root/2:2')
        self.assertIsNone(pick(scene,0,.75,.5)[0])

    def test_transform_flip_skew_and_origin(self):
        scene=scene_for([place(1,1,scale=(-1,1),skew=(.2,0),position=(32,8),origin=(4,4))],{'a':Image.new('RGBA',(16,16),'red')},64)
        key,bounds=pick(scene,0,.5,.125)
        self.assertEqual(key,'root/1:1')
        self.assertAlmostEqual(bounds[0],20/64)
        self.assertIsNone(pick(scene,0,.1,.8)[0])

    def test_nested_instances_have_distinct_ids(self):
        from tests.test_layers import fixture
        _,drawing,_=fixture()
        self.assertEqual(pick(drawing.scene,0,.1,.2)[0],'root/1:1/1:2')
        self.assertEqual(pick(drawing.scene,0,.5,.2)[0],'root/2:1/1:2')

    def test_empty_and_invalid_coordinates(self):
        scene=scene_for([place(1,1)],{'a':Image.new('RGBA',(4,4),'red')})
        for u,v in [(-.1,.5),(1,.5),(.5,math.nan),(.9,.9),(math.inf,0)]:
            with self.subTest(u=u,v=v):self.assertEqual(pick(scene,0,u,v),(None,None))

    def test_real_ifs_and_bin_picks_match_visible_pixels_in_both_modes(self):
        for relative in ['20_tricoro/data/graphic/test_effect_anime.ifs','14_GOLD/data/graph/sys/0200/owl.bin']:
            source=ROOT/relative
            if not source.exists():self.skipTest('Missing fixture')
            before=source.read_bytes()
            with tempfile.TemporaryDirectory() as folder:
                service=ResourceService(folder)
                try:
                    service.open(source)
                    for mode in ['cpu','gpu']:
                        preview=service.render(0,3,renderer=mode)
                        image=Image.open(preview['path']).convert('RGBA')
                        x,y=next((x,y) for y in range(0,image.height,3) for x in range(0,image.width,3) if image.getpixel((x,y))[3]>128)
                        request=dict(method='pick_layer',animation=0,frame=3,u=(x+.5)/image.width,v=(y+.5)/image.height,renderer=mode)
                        reply=service.dispatch(request)
                        self.assertIsNotNone(reply['selected'])
                        self.assertTrue(any(r['id']==reply['selected'] and r['visible'] for r in reply['layers']))
                        service.controls(0).set_visible(reply['selected'],False)
                        self.assertNotEqual(service.dispatch(request)['selected'],reply['selected'])
                        service.controls(0).set_visible('root',True)
                finally:service.close()
            self.assertEqual(source.read_bytes(),before)

    def test_stale_pick_is_rejected(self):
        scene=scene_for([place(1,1)],{'a':Image.new('RGBA',(4,4),'red')})
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder);service.accept_package(scene.package)
            try:
                with self.assertRaises(ValueError):service.dispatch(dict(method='pick_layer',animation=0,frame=0,u=.1,v=.1,package_id=-1))
            finally:service.close()
