"""Regression cases for game-hosted Layer composition and shared render paths."""
from dataclasses import replace
import tempfile
import unittest

from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.actions import Ref
from iidxviewer.afp import Animation
from iidxviewer.afp_hosts import canvas
from iidxviewer.playback import Playback
from iidxviewer.render import SceneRenderer
from iidxviewer.scene import Scene, clip_polygon
from iidxviewer.song_layers import GameLayerRenderer
from tests.test_playback import fixture, timeline, place
from tests.test_song_layers import pixels


def military_package():
    actor=timeline([[place(2,position=(4,4))],[(127,dict(flags=5,depth=1,position=(12,4)))],[],[]])
    p,a=fixture(actor,strings=['body'])
    actor_animation=Animation('01',a.movie,actor)
    movie=replace(a.movie,rect=(0,32,0,48));movie.string=a.movie.string
    marker=timeline([[place(2)]])
    movie.definitions=dict(movie.definitions)
    movie.definitions[3]=('sprite',marker)
    movie.definitions[4]=('image','virtual-marker')
    marker.tags=[place(4)]
    motion=timeline([[place(3,name=0,scale=(.5,1),position=(8,8))],[],[]])
    p.animations=[actor_animation,Animation('exp_aaa',movie,actor),*[Animation(f'm{i:02}',movie,motion) for i in range(11)]]
    return p


class LayerRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_aep_frame_gate_uses_child_clock_and_set_frame_is_not_a_seek(self):
        p,a=fixture(timeline([[place(2)],[],[]]));e=Playback(p,a,set());child=e.root.children[1]
        e.call(Ref('builtin','aep_set_frame_control'),Ref('builtin','aeplib'),[e.root,1,3],e.root)
        self.assertIsNone(child.render_spec())
        e.frame(1);self.assertIsNone(child.render_spec())
        e.frame(2);self.assertIsNotNone(child.render_spec())
        e.call(Ref('builtin','aep_set_set_frame'),Ref('builtin','aeplib'),[child,99],e.root)
        self.assertEqual(child.frame,2);self.assertEqual(child.set_frame_value,99)
        e.frame(0);self.assertIsNotNone(e.root.children[1].render_spec())
        e.close()

    def test_aeplib_recursive_seek_play_and_stop_reach_nested_compositions(self):
        p,a=fixture(timeline([[place(3)],[],[],[]]));a.movie.definitions[3]=('sprite',timeline([[place(2)],[],[],[]]))
        e=Playback(p,a,set());child=e.root.children[1];nested=child.children[1];lib=Ref('builtin','aeplib')
        e.call(Ref('builtin','deepGotoAndPlay'),lib,[child,3],e.root)
        self.assertEqual((child.frame,nested.frame),(2,2));self.assertTrue(nested.playing)
        e.call(Ref('builtin','deepStop'),lib,[child],e.root);e.frame(1)
        self.assertEqual((child.frame,nested.frame),(2,2));self.assertFalse(child.playing);self.assertFalse(nested.playing)
        e.call(Ref('builtin','play'),lib,[nested],e.root)
        self.assertFalse(child.playing);self.assertTrue(nested.playing)
        e.call(Ref('builtin','deepGotoAndPlay'),lib,[child],e.root)
        self.assertFalse(e.issues)
        child.timeline.labels=[(2,'loop')]
        e.call(Ref('builtin','deepGotoAndPlay'),lib,[child,'loop'],e.root)
        self.assertEqual((child.frame,nested.frame),(2,2));self.assertFalse(e.issues)
        e.close()

    def test_rect_masks_clip_uvs_after_affine_transforms_without_mask_textures(self):
        polygon=clip_polygon([(0,0,0,0),(8,0,1,0),(0,8,0,1)],((4,-1),(9,-1),(9,9),(4,9)))
        self.assertTrue(polygon);self.assertTrue(all(v[0]>=4 for v in polygon))
        self.assertTrue(all(abs(x/8-u)<1e-6 and abs(y/8-v)<1e-6 for x,y,u,v in polygon))
        p,a=fixture(timeline([[place(2)]]));r=SceneRenderer(p,a)
        try:
            child=r.scene.playback.root.children[1];child.rect_mask=(4,8,0,8)
            child.spec.update(position=(10,12))
            image=r.render(0)
            self.assertEqual(image.pixelColor(12,14).alpha(),0)
            self.assertEqual(image.pixelColor(15,14).red(),255)
            self.assertEqual(r.scene.masks,{})
            self.assertTrue(all(not d.masks for d in r.scene.draws(0)))
        finally:r.close()

    def test_many_independent_rect_masks_do_not_drop_later_layers(self):
        p,a=fixture(timeline([[place(3,depth=i+1,position=(i,0)) for i in range(80)]]))
        a.movie.definitions[3]=('sprite',timeline([[place(2)]]));s=Scene(p,a)
        try:
            for c in s.playback.root.children[1].children.values():c.rect_mask=(0,8,0,8)
            self.assertEqual(len(s.draws(0)),80)
            self.assertFalse(s.issues);self.assertEqual(s.masks,{})
        finally:s.close()

    def test_military_host_replaces_body_marker_and_uses_full_canvas(self):
        p=military_package();a=next(a for a in p.animations if a.name=='m00');r=SceneRenderer(p,a)
        try:
            self.assertEqual(canvas(p,a),(0,32,0,48));self.assertEqual(a.movie.rect,(0,32,0,48))
            first=r.render(0);second=r.render(1)
            self.assertTrue(first.pixelColor(11,14).alpha());self.assertFalse(first.pixelColor(5,5).alpha())
            self.assertNotEqual(bytes(first.constBits()),bytes(second.constBits()))
            self.assertFalse(r.issues);self.assertNotIn('virtual-marker',[d.texture for d in r.scene.draws(0)])
        finally:r.close()
        r=GameLayerRenderer(p,dict(song_id=15023),[0,1,2],'cpu')
        try:
            self.assertEqual(p.animations[r.cues[0][2]].name,'m10')
            frames=[0,2,3,7,20,2];images=[pixels(r.render(f,(64,64))) for f in frames]
            self.assertEqual(images[1],images[-1]);self.assertNotEqual(images[0],images[2])
            self.assertTrue(all(r.cues[i][1]==r.cues[i+1][0] for i in range(12)))
        finally:r.close()

    def test_subtractive_additive_max_min_and_default_mode_have_defined_pixels(self):
        for mode in [4,8,79,9,70,5,6,13]:
            p,a=fixture(timeline([[place(3,depth=1),place(2,depth=2,blend=mode)]]))
            a.movie.definitions[3]=('image','base')
            p.texture=lambda name:Image.new('RGBA',(8,8),(40,60,80,255) if name=='pixel' else (100,100,100,255))
            r=SceneRenderer(p,a)
            try:
                pixel=r.render(0).pixelColor(2,2).getRgb()
                rgb=(140,160,180) if mode in [4,8,79] else (60,40,20) if mode in [9,70] else (100,100,100) if mode==5 else (40,60,80)
                self.assertEqual(pixel,(*rgb,255),(mode,pixel));self.assertFalse(r.issues)
            finally:r.close()
        p,a=fixture(timeline([[place(2,blend=8)]]));p.texture=lambda name:Image.new('RGBA',(8,8),(0,0,0,255))
        r=SceneRenderer(p,a)
        try:self.assertEqual(r.render(0).pixelColor(2,2).alpha(),0)
        finally:r.close()

    def test_preview_dimensions_and_picking_share_the_host_canvas(self):
        from iidxviewer.picking import pick
        from iidxviewer.service import ResourceService
        p=military_package();index=2
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder);service.accept_package(p)
            try:
                info=service.animation_info(index,p.animations[index])
                self.assertEqual((info['width'],info['height']),(32,48))
                result=service.render(index,0,renderer='cpu',transport='shared')
                self.assertEqual((result['width'],result['height']),(32,48))
                scene=service.renderers[index,'cpu'].scene
                self.assertIsNotNone(pick(scene,0,11/32,14/48)[0])
                self.assertIsNone(pick(scene,0,1/32,1/48)[0])
            finally:service.close()

    def test_gpu_uses_same_host_geometry_masks_and_blend_modes(self):
        from iidxviewer.gpu import GpuDevice,GpuSceneRenderer
        try:device=GpuDevice()
        except Exception as e:self.skipTest('Hardware GPU unavailable: '+str(e))
        try:
            for mode in [0,3,4,5,6,8,9,13,70,79]:
                p,a=fixture(timeline([[place(3,depth=1),place(2,depth=2,blend=mode,multiply=(1,1,1,.6))]]))
                a.movie.definitions[3]=('image','base')
                p.texture=lambda name:Image.new('RGBA',(8,8),(40,60,80,255) if name=='pixel' else (100,100,100,255))
                cpu,gpu=SceneRenderer(p,a),GpuSceneRenderer(p,a,device)
                try:
                    for r in [cpu,gpu]:r.scene.playback.root.children[1].rect_mask=(2,7,2,7)
                    c,g=cpu.render(0),gpu.render(0)
                    for point in [(0,0),(4,4),(12,12)]:
                        for expected,actual in zip(c.pixelColor(*point).getRgb(),g.pixelColor(*point).getRgb()):
                            self.assertLessEqual(abs(expected-actual),2,(mode,point,expected,actual))
                finally:cpu.close();gpu.close();device.reset_textures()
            p=military_package();a=p.animations[2];cpu,gpu=SceneRenderer(p,a),GpuSceneRenderer(p,a,device)
            try:
                for f in [0,1,2,0]:
                    c,g=cpu.render(f),gpu.render(f)
                    self.assertEqual(c.size(),g.size())
                    for point in [(0,0),(11,14),(18,14)]:self.assertEqual(c.pixelColor(*point),g.pixelColor(*point))
            finally:cpu.close();gpu.close()
        finally:device.close()
