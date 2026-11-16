import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import json
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.afp import Animation, Movie
from iidxviewer.layers import LayerControls
from iidxviewer.render import SceneRenderer
from iidxviewer.resources import Package
from iidxviewer.service import ResourceService
from tests.test_playback import timeline, place

ROOT = Path(os.environ.get('IIDX_GAME_ROOT', Path(__file__).resolve().parents[2]))

def fixture(mask=False):
    child = timeline([[place(2)], []])
    root = timeline([[place(1, 1, clip=2) if mask else place(1, 1), place(1, 2, position=(12, 0))], []])
    movie = Movie('layers', root, (0,32,0,16), 60, {1:('sprite',child), 2:('image','pixel')}, [(1,'part')])
    animation = Animation('layers', movie, root)
    package = Package(Path('layers.ifs'), animations=[animation])
    package.texture = lambda _: Image.new('RGBA',(8,8),(255,40,20,255))
    drawing = SceneRenderer(package, animation)
    controls = LayerControls(package, animation); drawing.scene.controls = controls
    controls.discover(drawing.scene, 0)
    return package, drawing, controls

class LayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_same_texture_instances_and_groups_are_independent(self):
        _, drawing, controls = fixture()
        original = drawing.render(0)
        controls.set_visible('root/1:1/1:2',False)
        image = drawing.render(0)
        self.assertEqual(image.pixelColor(3,3).alpha(),0)
        self.assertGreater(image.pixelColor(15,3).alpha(),0)
        controls.set_visible('root/2:1',False)
        image = drawing.render(0)
        self.assertFalse(any(image.pixelColor(x,3).alpha() for x in range(32)))
        controls.set_visible('root',True)
        image = drawing.render(0)
        self.assertEqual(bytes(image.constBits()),bytes(original.constBits()))

    def test_hidden_mask_does_not_reveal_content_or_stop_clocks(self):
        _, drawing, controls = fixture(mask=True)
        controls.set_visible('root/1:1',False)
        image = drawing.render(0)
        self.assertFalse(any(image.pixelColor(x,3).alpha() for x in range(32)))
        controls.set_visible('root',False); drawing.render(1)
        self.assertEqual(drawing.scene.playback.tick,1)
        self.assertEqual(drawing.scene.playback.root.children[1].frame,1)

    def test_dynamic_clone_and_depth_swap_preserve_identity(self):
        _, drawing, controls = fixture(); engine=drawing.scene.playback
        first=engine.root.children[1]; clone=engine.clone(first,'copy',10)
        controls.discover(drawing.scene,0)
        self.assertNotEqual(first.layer_key,clone.layer_key)
        controls.set_visible(first.layer_key,False)
        engine.root.children[1],engine.root.children[2]=engine.root.children[2],first
        self.assertEqual(len(drawing.scene.draws(0)),2)
        self.assertTrue(controls.visible(clone.layer_key))

    def test_cache_modes_animation_switch_and_export(self):
        source=ROOT/'20_tricoro/data/graphic/test_effect_anime.ifs'
        if not source.exists():self.skipTest('Missing IFS fixture')
        before=hashlib.sha256(source.read_bytes()).digest()
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder)
            try:
                service.open(source); state=service.animation_layers(0,3)
                self.assertGreater(len(state['layers']),1)
                pixels=Image.open(service.render(0,3,renderer='cpu')['path']).copy()
                service.dispatch(dict(method='set_layer_visible',animation=0,layer='root',visible=False,package_id=state['package_id']))
                for mode in ['cpu','gpu']:
                    self.assertIsNone(Image.open(service.render(0,3,renderer=mode)['path']).getbbox())
                service.render(1,3)
                self.assertIn('root',service.controls(0).hidden)
                output=Path(folder)/'hidden.png'
                service.dispatch(dict(method='export',animation=0,kind='frame',frame=3,target=str(output)))
                self.assertIsNone(Image.open(output).getbbox())
                service.controls(0).set_visible('root',True)
                self.assertEqual(Image.open(service.render(0,3,renderer='cpu')['path']).tobytes(),pixels.tobytes())
            finally:service.close()
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)

    def test_legacy_nested_switches(self):
        from iidxviewer.legacy_idx import IndexData, LegacyAnimation
        def record(kind, ident):
            return dict(type=kind,id=ident,flags=0,duration=0,begin=0,end=5,base=0,anchor=(0,0),position=[],scale=[],alpha=[],rotation=[])
        index=IndexData(cells=[(0,0,8,8)],records=[record(2,2),record(-1,0),record(0,0),record(-1,0)])
        animation=LegacyAnimation('nested',index,0,{0:'pixel'})
        package=Package(Path('nested.idx'),animations=[animation]);package.texture=lambda _:Image.new('RGBA',(8,8),'red')
        drawing=SceneRenderer(package,animation);control=LayerControls(package,animation);drawing.scene.controls=control
        control.load();control.set_visible('root/0/2',False)
        self.assertEqual(drawing.scene.draws(0),[])
        control.set_visible('root/0/2',True)
        self.assertEqual(len(drawing.scene.draws(0)),1)
        source=ROOT/'14_GOLD/data/graph/sys/0200/owl.bin'
        if not source.exists():self.skipTest('Missing legacy fixture')
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder)
            try:
                service.open(source);state=service.animation_layers(0,3);control=service.controls(0)
                self.assertTrue(any(r['level']>=1 for r in state['layers']))
                row=next(r for r in state['layers'] if r['detail'].startswith('tex/'))
                control.set_visible('root',False);control.set_visible(row['id'],True)
                self.assertTrue(control.visible(row['id']))
                for mode in ['cpu','gpu']:
                    control.set_visible('root',False)
                    self.assertIsNone(Image.open(service.render(0,3,renderer=mode)['path']).getbbox())
            finally:service.close()

    def test_invalid_and_stale_changes_are_rejected(self):
        package,_,controls=fixture()
        with self.assertRaises(ValueError):controls.set_visible('missing',False)
        self.assertFalse(controls.hidden)
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder);service.accept_package(package)
            try:
                with self.assertRaises(ValueError):service.dispatch(dict(method='set_layer_visible',animation=0,layer='root',visible=False,package_id=-1))
                self.assertFalse(service.controls(0).hidden)
                service.controls(0).set_visible('root',False)
                for kind in ['webp','sequence']:
                    output=Path(folder)/kind
                    service.dispatch(dict(method='export',animation=0,kind=kind,target=str(output)))
                    image=Image.open(output if kind=='webp' else next(output.glob('*.png')))
                    self.assertIsNone(image.getbbox())
            finally:service.close()

    def test_native_protocol_request_ids_do_not_replace_layer_ids(self):
        source=ROOT/'20_tricoro/data/graphic/test_effect_anime.ifs'
        if not source.exists():self.skipTest('Missing IFS fixture')
        home=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            requests=[dict(id=1,method='open',path=str(source)),dict(id=2,method='animation_layers',animation=0,frame=3),
                      dict(id=3,method='set_layer_visible',animation=0,layer='root',visible=False),
                      dict(id=4,method='render',animation=0,frame=3,renderer='cpu')]
            result=subprocess.run([sys.executable,str(home/'backend.py'),'--cache',folder],
                input=''.join(json.dumps(r)+'\n' for r in requests),capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            replies=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([r['id'] for r in replies],[1,2,3,4])
            self.assertEqual(replies[1]['result']['layers'][0]['detail'], '动画')
            self.assertTrue(all(r['ok'] for r in replies),replies)
            self.assertIsNone(Image.open(replies[-1]['result']['path']).getbbox())
