from pathlib import Path
from types import SimpleNamespace
import math
import os
import struct
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PySide6.QtGui import QGuiApplication, QImage
from iidxviewer.actions import Program, Ref, UNDEFINED
from iidxviewer.afp import AP2, Animation, Movie, Timeline, display_list
from iidxviewer.engines import PE, detect, family
from iidxviewer.frames import SharedFrames
from iidxviewer.playback import Playback
from iidxviewer.resources import Asset, Package, open_package
from iidxviewer.scene import Scene


def script(*instructions, strings=()):
    return b'\xff\x01'+struct.pack('<H',len(strings))+b''.join(struct.pack('<H',i) for i in range(len(strings)))+b''.join(instructions)+b'\0'


def push(*values):
    data=b''
    for value in values:
        if value=='THIS':data+=b'\x0c'
        elif isinstance(value,tuple) and value[0]=='string':data+=b'\x08'+bytes([value[1]])
        elif isinstance(value,tuple) and value[0]=='register':data+=b'\x04'+bytes([value[1]])
        elif isinstance(value,bool):data+=b'\x05' if value else b'\x06'
        elif isinstance(value,int):data+=b'\x07'+struct.pack('>i',value)
        else:data+=b'\x01'+struct.pack('>f',value)
    return b'\x43'+bytes([len(values)])+data


def timeline(frames):
    tags=[];index=[]
    for entries in frames:
        index.append((len(tags),len(entries)));tags.extend(entries)
    return Timeline(index,tags)


def place(character, depth=1, **extra):
    flags=2 | (4 if {'scale','skew','position'} & extra.keys() else 0) | (8 if {'multiply','add'} & extra.keys() else 0)
    return 127,dict(flags=flags,depth=depth,character=character,**extra)


def fixture(child, root_actions=(), strings=()):
    root=timeline([[place(1),*[(122,a) for a in root_actions]]])
    movie=Movie('fixture',root,(0,64,0,64),30,{1:('sprite',child),2:('image','pixel')},[(1,'child')])
    movie.string=lambda ref:strings[ref]
    animation=Animation('fixture',movie,root)
    package=Package(Path('fixture.ifs'))
    package.texture=lambda name:Image.new('RGBA',(8,8),(255,40,20,255)) if name=='pixel' else None
    return package,animation


class PlaybackTests(unittest.TestCase):
    def test_packed_rgba_alpha_does_not_tint_fades_blue(self):
        parser=AP2(bytes(56),'colors')
        data=struct.pack('<IHHH2xII',0x6002,1,0,2,0xffffff80,0x12345678)
        placement=parser.placement(data)
        self.assertEqual(placement['multiply'],(1,1,1,128/255))
        self.assertEqual(placement['add'],tuple(v/255 for v in (0x12,0x34,0x56,0x78)))

    def test_stopped_parent_keeps_child_clock_running(self):
        child=timeline([[place(2)],[(127,{'flags':5,'depth':1,'position':(8,0)})],[(127,{'flags':5,'depth':1,'position':(16,0)})]])
        p,a=fixture(child,[script(b'\x04')]);engine=Playback(p,a,set())
        root=engine.frame(2)
        self.assertFalse(root.playing)
        self.assertEqual(root.frame,0)
        self.assertEqual(root.children[1].frame,2)

    def test_stop_child_and_deterministic_seek(self):
        child=timeline([[place(2),(122,script(b'\x04'))],[(127,{'flags':5,'depth':1,'position':(8,0)})]])
        p,a=fixture(child);engine=Playback(p,a,set())
        self.assertEqual(engine.frame(9).children[1].frame,0)
        self.assertEqual(engine.frame(0).children[1].frame,0)
        self.assertEqual(engine.frame(9).children[1].frame,0)

    def test_image_enter_frame_event_changes_transform(self):
        code=script(push('THIS',('string',0),30),b'\x2f',strings=['_x'])
        p,a=fixture(timeline([[place(2,events=[(2,code)])]]),strings=['_x'])
        engine=Playback(p,a,set());image=engine.frame(1).children[1].children[1]
        self.assertEqual(image.render_spec()['position'],(30,0))

    def test_new_placement_restarts_child_on_parent_loop(self):
        child=timeline([[place(2)],[]]);p,a=fixture(child)
        grandchild=timeline([[place(3)],[],[]]);a.movie.definitions[2]=('sprite',grandchild);a.movie.definitions[3]=('image','pixel')
        engine=Playback(p,a,set());first=engine.root.children[1].children[1]
        self.assertEqual(engine.frame(1).children[1].children[1].frame,1)
        current=engine.frame(2).children[1].children[1]
        self.assertIsNot(current,first);self.assertEqual(current.frame,0)

    def test_goto_numeric_and_label_stop(self):
        for target in (3,('string',0)):
            child=timeline([[place(2),(122,script(push(target),b'\x47\x01',strings=['end']))],[],[]])
            child.labels=[(2,'end')]
            p,a=fixture(child,strings=['end']);engine=Playback(p,a,set())
            self.assertEqual(engine.root.children[1].frame,2)
            self.assertFalse(engine.root.children[1].playing)

    def test_set_instance_properties_and_registers(self):
        code=script(push('THIS'),b'\x3f\x01\x01',push(('string',0),25),b'\x2f',
                    push(('register',1),('string',1),False),b'\x2f',strings=['_alpha','_visible'])
        p,a=fixture(timeline([[place(2),(122,code)]]),strings=['_alpha','_visible'])
        engine=Playback(p,a,set());clip=engine.root.children[1]
        self.assertEqual(clip.variables['_alpha'],25)
        self.assertFalse(clip.variables['_visible'])
        self.assertIsNone(clip.render_spec())

    def test_method_goto_and_flash_depth_offset(self):
        child=timeline([[place(2,depth=1)],[],[]]);p,a=fixture(child)
        engine=Playback(p,a,set());clip=engine.root.children[1]
        target=engine.call(Ref('builtin','getInstanceAtDepth'),clip,[-16383],clip)
        self.assertIs(target,clip.children[1])
        engine.call(Ref('method','gotoAndStop'),clip,[3],clip);engine.frame(0)
        engine.settle(engine.root)
        self.assertEqual(clip.frame,2)

    def test_function_parameters_and_register_values(self):
        p,a=fixture(timeline([[]]),strings=['x'])
        engine=Playback(p,a,set())
        body=script(push(('register',1),2),b'\x27\x1f')
        define=b'\x40'+struct.pack('>HHHBH',0,0,1,2,len(body))+struct.pack('>BH',1,0)+body
        program=Program(script(define,b'\x1f'),a.movie.string)
        function=engine.vm.execute(program,engine.root)
        self.assertEqual(engine.vm.execute(function.program,engine.root,[40],function),42)

    def test_conditional_branch_and_backwards_jump_budget(self):
        p,a=fixture(timeline([[]]));engine=Playback(p,a,set())
        # Branch skips a PUSH integer and returns 7 instead.
        program=Program(script(push(True),b'\x46\x00\x07',push(99),push(7),b'\x1f'),a.movie.string)
        self.assertEqual(engine.vm.execute(program,engine.root),7)
        loop=Program(b'\xff\0\x44\xff\xfd',a.movie.string)
        engine.budget=30
        with self.assertRaisesRegex(ValueError,'数量超限'):engine.vm.execute(loop,engine.root)

    def test_malformed_script_rejected(self):
        for data in [b'',b'\xff',b'\xff\x01\xff\xff',b'\xff\x00\x43\x01\x07',b'\xff\x00\x44\x7f\xff']:
            with self.subTest(data=data),self.assertRaises((ValueError,struct.error)):Program(data,str)

    def test_goto_cycle_is_bounded(self):
        child=timeline([[place(2),(122,script(push(2),b'\x47\x00'))],[(122,script(push(1),b'\x47\x00'))]])
        p,a=fixture(child);issues=set();engine=Playback(p,a,issues)
        self.assertTrue(any('跳转超出' in issue for issue in issues))

    def test_display_cache_applies_matrix_group_and_random_seek(self):
        tl=timeline([[place(2,position=(1,2))],[(127,{'flags':5,'depth':1,'scale':(2,3)})],[(128,1)]])
        self.assertNotIn('position',display_list(tl,1)[1])
        self.assertEqual(display_list(tl,2),{})
        self.assertNotIn('scale',display_list(tl,0)[1])
        self.assertEqual(display_list(tl,1)[1]['scale'],(2,3))

    def test_colour_group_resets_omitted_multiply_and_orphan_move_is_ignored(self):
        tl=timeline([[place(2,multiply=(1,.5,.5,.2))],[(127,{'flags':9,'depth':1,'add':(.1,0,0,0)})],
                     [(128,1),(127,{'flags':5,'depth':1,'position':(2,3)})]])
        self.assertNotIn('multiply',display_list(tl,1)[1])
        self.assertEqual(display_list(tl,2),{})

    def test_swapped_depths_and_script_removal_survive_empty_timeline_frames(self):
        child=timeline([[place(2,depth=1),place(3,depth=2)],[],[],[]])
        p,a=fixture(child);a.movie.definitions[3]=('image','blue')
        e=Playback(p,a,set());clip=e.root.children[1]
        red,blue=clip.children[1],clip.children[2]
        e.call(Ref('method','swapDepths'),red,[blue],clip)
        e.frame(1)
        self.assertIs(clip.children[2],red);self.assertIs(clip.children[1],blue)
        e.remove(red);e.frame(2)
        self.assertNotIn(red,clip.children.values())
        e.frame(0)
        self.assertEqual(e.root.children[1].children[1].spec['character'],2)

    def test_authored_matrix_update_replaces_script_transform(self):
        child=timeline([[place(2)],[(127,{'flags':5,'depth':1,'position':(12,3)})]])
        p,a=fixture(child);e=Playback(p,a,set());image=e.root.children[1].children[1]
        image.variables['_x']=100
        e.frame(1)
        self.assertEqual(image.render_spec()['position'],(12,3))

    def test_all_five_families_and_pe_bounds(self):
        self.assertEqual([family(n) for n in [9,17,18,19,20,24,25,26,27,34]],['9-17','9-17','18-19','18-19','20-24','20-24','25-26','25-26','27+','27+'])
        for data in [b'',b'MZ'+bytes(100),b'MZ'+bytes(58)+b'\xff'*4]:
            with self.assertRaises((ValueError,struct.error)):PE(data)

    def test_sdvx_uses_modern_playback_group(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ['SDVX6','SOUND_VOLTEX','Sound Voltex']:
                info=detect(Path(folder)/name/'data/graphics/title.ifs')
                self.assertEqual(info['family'],'27+');self.assertFalse(info['executes_game_code'])

    def test_pe_export_inspection_uses_data_without_executing(self):
        data=bytearray(1024);data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
        data[128:132]=b'PE\0\0';struct.pack_into('<HH',data,132,0x14c,1);struct.pack_into('<H',data,148,224)
        optional=152;struct.pack_into('<H',data,optional,0x10b)
        struct.pack_into('<II',data,optional+96,0x1000,512)
        data[376:384]=b'.rdata\0\0';struct.pack_into('<IIII',data,384,512,0x1000,512,512)
        struct.pack_into('<IIHHIIIIIII',data,512,0,0,0,0,0,1,1,1,0x1050,0x1060,0x1070)
        struct.pack_into('<I',data,592,0x1090);struct.pack_into('<I',data,608,0x1080)
        data[640:649]=b'afp_boot\0'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'20_test/libafp-win32.dll';path.parent.mkdir();path.write_bytes(data)
            info=detect(path)
            self.assertEqual(info['family'],'20-24');self.assertFalse(info['executes_game_code'])
            self.assertEqual(info['library']['architecture'],'x86');self.assertEqual(info['library']['export_count'],1)


class SharedAndGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_shared_ring_wrap_and_premultiplied_bgra(self):
        with tempfile.TemporaryDirectory() as folder:
            ring=SharedFrames(folder)
            try:
                image=QImage(4,3,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0xffe03010)
                slots=[]
                for _ in range(4):
                    reply=ring.write(image);slots.append(reply['offset'])
                    self.assertEqual(ring.memory[reply['offset']:reply['offset']+4],b'\x10\x30\xe0\xff')
                self.assertEqual(slots[0],slots[3]);self.assertEqual(len(set(slots[:3])),3)
            finally:ring.close()
            self.assertFalse((Path(folder)/'preview.frames').exists())

    def test_preview_resolution_renderer_switch_and_cache_isolation(self):
        from iidxviewer.service import ResourceService
        p,a=fixture(timeline([[place(2)]]));a.movie.rect=(0,1920,0,1080);p.animations=[a]
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder);service.package=p
            try:
                hd=service.render(0,0,transport='shared',resolution=1080,renderer='cpu')
                self.assertEqual((hd['width'],hd['height']),(1920,1080))
                self.assertEqual(hd['bytes'],1920*1080*4)
                self.assertEqual(hd['renderer']['api'],'CPU');self.assertFalse(service.gpu_attempted)
                before=service.shared.memory[hd['offset']:hd['offset']+hd['bytes']]
                small=service.render(0,0,transport='shared',resolution=720,renderer='cpu')
                self.assertEqual((small['width'],small['height']),(1280,720))
                accelerated=service.render(0,0,transport='shared',resolution=720,renderer='gpu')
                self.assertTrue(service.gpu_attempted)
                self.assertEqual((accelerated['width'],accelerated['height']),(1280,720))
                again=service.render(0,0,transport='shared',resolution=1080,renderer='cpu')
                self.assertFalse(again['renderer']['hardware'])
                self.assertEqual(before,service.shared.memory[again['offset']:again['offset']+again['bytes']])
            finally:service.close()

    def test_invalid_preview_settings_rejected(self):
        from iidxviewer.service import ResourceService
        with tempfile.TemporaryDirectory() as folder:
            service=ResourceService(folder)
            try:
                for settings in [{'resolution':1440},{'renderer':'unknown'}]:
                    with self.assertRaises(ValueError):service.render(0,0,**settings)
                self.assertFalse(service.gpu_attempted)
            finally:service.close()

    def test_portrait_and_480p_preview_bounds(self):
        from iidxviewer.service import ResourceService
        for preset,source,expected in [('480p',(1920,1440),(640,480)),
                                       ('720p-portrait',(1080,1920),(720,1280)),
                                       ('1080p-portrait',(1080,1920),(1080,1920))]:
            with self.subTest(preset=preset),tempfile.TemporaryDirectory() as folder:
                p,a=fixture(timeline([[place(2)]]));a.movie.rect=(0,source[0],0,source[1]);p.animations=[a]
                service=ResourceService(folder);service.package=p
                try:
                    result=service.render(0,0,transport='shared',resolution=preset,renderer='cpu')
                    self.assertEqual((result['width'],result['height']),expected)
                    self.assertEqual(result['bytes'],expected[0]*expected[1]*4)
                finally:service.close()

    def test_hardware_shader_pixels_match_cpu(self):
        from iidxviewer.gpu import GpuDevice,GpuSceneRenderer,GpuUnavailable
        from iidxviewer.render import SceneRenderer
        try:device=GpuDevice()
        except Exception as error:self.skipTest('Hardware GPU unavailable: '+str(error))
        try:
            p,a=fixture(timeline([[place(2,position=(12,10),multiply=(.5,1,1,.5))]]))
            cpu=SceneRenderer(p,a).render(0);gpu=GpuSceneRenderer(p,a,device).render(0)
            self.assertTrue(device.info['hardware'])
            for point in [(0,0),(14,12),(40,40)]:
                c,g=cpu.pixelColor(*point),gpu.pixelColor(*point)
                for expected,actual in zip(c.getRgb(),g.getRgb()):self.assertLessEqual(abs(expected-actual),2)
            uploads=device.upload_count;GpuSceneRenderer(p,a,device).render(0)
            self.assertEqual(device.upload_count,uploads)
        finally:device.close()

    def test_gpu_nested_masks_and_blends_match_reference(self):
        from iidxviewer.gpu import GpuDevice,GpuSceneRenderer
        from iidxviewer.render import SceneRenderer
        try:device=GpuDevice()
        except Exception as error:self.skipTest('Hardware GPU unavailable: '+str(error))
        try:
            child=timeline([[place(2,depth=1,clip=3),place(2,depth=2,clip=3,position=(4,0)),
                             place(3,depth=3),place(3,depth=4,position=(20,0))]])
            p,a=fixture(child)
            a.movie.definitions[3]=('image','blue')
            p.texture=lambda name:Image.new('RGBA',(8,8),(255,255,255,255)) if name=='pixel' else Image.new('RGBA',(16,16),(0,0,255,255))
            cpu=SceneRenderer(p,a).render(0);gpu=GpuSceneRenderer(p,a,device).render(0)
            self.assertEqual(gpu.pixelColor(2,2).alpha(),0)
            self.assertEqual(gpu.pixelColor(6,2).blue(),255)
            self.assertEqual(gpu.pixelColor(24,2).blue(),255)
            for point in [(2,2),(6,2),(12,2),(24,2)]:self.assertEqual(cpu.pixelColor(*point),gpu.pixelColor(*point))
            for blend in [3,4,8]:
                child=timeline([[place(3,depth=1,multiply=(1,1,1,.4)),place(2,depth=2,blend=blend,multiply=(1,.5,.2,.5))]])
                p,a=fixture(child);a.movie.definitions[3]=('image','blue')
                p.texture=lambda name:Image.new('RGBA',(8,8),(255,40,20,255)) if name=='pixel' else Image.new('RGBA',(16,16),(0,0,255,255))
                device.reset_textures()
                cpu=SceneRenderer(p,a).render(0);gpu=GpuSceneRenderer(p,a,device).render(0)
                for expected,actual in zip(cpu.pixelColor(5,2).getRgb(),gpu.pixelColor(5,2).getRgb()):
                    self.assertLessEqual(abs(expected-actual),2,(blend,expected,actual))
        finally:device.close()


if __name__=='__main__':unittest.main()
