import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PySide6.QtGui import QGuiApplication
from iidxviewer.live2d import LiveDocument, open_live2d, LiveScene
from iidxviewer.service import ResourceService

ROOT=Path(os.environ.get('IIDX_LIVE2D_ROOT',Path(__file__).resolve().parents[3]/'SDVX/data/graphics/live2d'))

@unittest.skipUnless((ROOT/'rasis_ver7/rasis_ver7.model3.json').exists(),'Live2D fixtures unavailable')
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=ResourceService(Path(self.tmp.name))
        self.path=ROOT/'rasis_ver7/rasis_ver7.model3.json';self.service.open(self.path);self.doc=self.service.package.archive
        self.moc_hash=hashlib.sha256(self.doc.moc_path.read_bytes()).digest()
    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.doc.moc_path.read_bytes()).digest(),self.moc_hash)
        self.service.close();self.tmp.cleanup()
    def edit(self,**args):return self.service.dispatch(dict(method='live2d_edit',**args))
    def test_versions_and_repeat_open(self):
        examples={}
        for p in ROOT.rglob('*.model3.json'):
            try:
                refs=json.loads(p.read_text(encoding='utf-8-sig'))['FileReferences']
                m=p.parent/refs['Moc'];version=m.read_bytes()[4]
                examples.setdefault(version,p)
            except (OSError,KeyError):continue
        for _ in range(2):
            for version,p in examples.items():
                package=open_live2d(p)
                self.assertEqual(package.archive.moc.version,version)
                for animation in package.animations[:3]:
                    scene=LiveScene(package,animation);scene.draws(0);scene.draws(min(30,animation.length-1))
                package.close()
        self.assertTrue({1,2,3,5}.issubset(examples))
    def test_motion_changes_geometry_and_backward_seek(self):
        animation=next(a for a in self.service.package.animations if a.group=='Idle')
        self.doc.evaluate(animation,0);first=np.concatenate(self.doc.core.positions).copy()
        self.doc.evaluate(animation,90);later=np.concatenate(self.doc.core.positions).copy()
        self.assertFalse(np.allclose(first,later))
        self.doc.evaluate(animation,0);np.testing.assert_allclose(np.concatenate(self.doc.core.positions),first,atol=1e-5)
    def test_mesh_uv_deformer_triangle_undo(self):
        original=bytes(self.doc.moc.data)
        for section,index in [('meshes',0),('uvs',0),('warp',0)]:
            points,_=self.doc.moc.points(section,index)
            self.edit(operation='point',section=section,index=index,vertex=0,x=float(points[0,0]+.01),y=float(points[0,1]))
            self.assertNotEqual(bytes(self.doc.moc.data),original)
            self.edit(operation='undo');self.assertEqual(bytes(self.doc.moc.data),original)
        indices=self.doc.moc.triangles(0)[0].tolist()
        self.edit(operation='triangle',index=0,triangle=0,vertices=indices[::-1])
        self.edit(operation='undo');self.assertEqual(bytes(self.doc.moc.data),original)
        self.edit(operation='field',section='rotation_forms',index=0,field='angle',value=12)
        self.edit(operation='undo');self.assertEqual(bytes(self.doc.moc.data),original)
        with self.assertRaises(ValueError):self.edit(operation='parent',section='deformers',index=0,parent=0)
        with self.assertRaises(ValueError):self.edit(operation='triangle',index=0,triangle=0,vertices=[999999,0,1])
        self.assertEqual(bytes(self.doc.moc.data),original)
    def test_json_reload_motion_and_save_reopen(self):
        name=next(n for n in self.doc.json_files if n.endswith('.motion3.json'))
        value=json.loads(json.dumps(self.doc.json_files[name]));value['Meta']['Duration']+=1
        self.edit(operation='json',document=name,text=json.dumps(value))
        target=Path(self.tmp.name)/'edited'
        self.service.dispatch(dict(method='live2d_save',target=str(target)))
        reopened=open_live2d(target/self.path.name)
        self.assertEqual(reopened.archive.json_files[name]['Meta']['Duration'],value['Meta']['Duration'])
        reopened.close()
        self.edit(operation='undo');self.assertNotEqual(self.doc.json_files[name]['Meta']['Duration'],value['Meta']['Duration'])
        with self.assertRaises(ValueError):self.doc.save(self.path.parent/'forbidden')
        with self.assertRaises(FileExistsError):self.doc.save(target)
    def test_parameter_edit_export_and_originals_unchanged(self):
        p=self.doc.info()['parameters'][0];value=(p['minimum']+p['maximum'])/2
        self.edit(operation='parameter',index=0,value=value)
        self.assertEqual(self.doc.moc.record('parameters',0)['default'],value)
        self.assertTrue(self.service.info()['modified'])
        target=Path(self.tmp.name)/'model-copy';self.doc.save(target)
        reopened=open_live2d(target/self.path.name)
        self.assertEqual(reopened.archive.moc.record('parameters',0)['default'],value);reopened.close()
        self.edit(operation='undo');self.assertFalse(self.service.info()['modified'])
    def test_shared_gpu_layer_mask_and_edit_invalidation(self):
        first=self.service.render(1,0,transport='shared',resolution='480p')
        self.assertEqual(first['kind'],'raw')
        self.assertTrue(first['renderer']['hardware'])
        scene=self.service.renderers[1,'gpu'].scene
        for draws in scene.masks.values():self.assertTrue(all(not d.masks for d in draws))
        self.service.dispatch(dict(method='set_layer_visible',animation=1,layer='root',visible=False))
        self.service.render(1,0,transport='shared',resolution='480p')
        image=list(self.service.raw_frames.values())[-1]
        self.assertFalse(np.frombuffer(image.constBits(),np.uint8)[3::4].any())
    def test_keyform_pose_and_opacity_curves(self):
        result=self.service.dispatch(dict(method='live2d_details',section='meshes',index=0,form=1))
        self.assertTrue(result['keys'])
        before=bytes(self.doc.moc.data)
        self.edit(operation='pose',keys=result['keys'])
        self.assertEqual(bytes(self.doc.moc.data),before)
        animation=next(a for a in self.service.package.animations if a.group=='Idle')
        name=self.doc.refs['Motions'][animation.group][animation.motion]['File']
        motion=json.loads(json.dumps(self.doc.json_files[name]));motion['Meta'].update(Duration=2,Loop=False)
        part=self.doc.core.part_ids[0]
        motion['Curves']=[{'Target':'Model','Id':'Opacity','Segments':[0,0,0,1,1]},
                          {'Target':'PartOpacity','Id':part,'Segments':[0,0,0,1,1]}]
        self.edit(operation='json',document=name,text=json.dumps(motion))
        self.doc.evaluate(animation,0);self.assertEqual(self.doc.opacity,0)
        self.doc.evaluate(animation,30);self.assertAlmostEqual(self.doc.opacity,.5);self.assertAlmostEqual(float(self.doc.core.parts[0]),.5)
        defaults=bytes(self.doc.moc.data);self.edit(operation='release')
        self.assertFalse(self.doc.values);self.assertEqual(bytes(self.doc.moc.data),defaults)

    def test_texture_undo_snapshot_and_visibility(self):
        from PIL import Image
        scene=LiveScene(self.service.package,self.service.package.animations[0])
        initial=scene.draws(0);index=int(initial[0].layer.split(':')[-1])
        self.edit(operation='field',section='meshes',index=index,field='visible',value=0)
        self.assertNotIn(f'root/mesh:{index}',[d.layer for d in scene.draws(0)])
        self.edit(operation='undo');self.assertIn(f'root/mesh:{index}',[d.layer for d in scene.draws(0)])
        texture=Path(self.tmp.name)/'green.png';Image.new('RGBA',(4,4),'green').save(texture)
        self.edit(operation='texture',index=0,path=str(texture))
        name=self.doc.textures[0].relative_to(self.doc.root).as_posix()
        asset=next(a for a in self.service.package.assets if a.name==name)
        self.assertEqual(asset.read(),texture.read_bytes())
        context=self.service.dispatch(dict(method='export_context',animation=0))
        snapshot=open_live2d(context['path'])
        self.assertEqual(snapshot.texture('live2d/0').getpixel((0,0)),(0,128,0,255));snapshot.close()
        self.edit(operation='undo');self.assertEqual(asset.source,self.doc.textures[0])

    def test_stdio_protocol_keeps_sdk_output_off_stdout(self):
        home=Path(__file__).resolve().parents[1]
        requests=[dict(id=1,method='open',path=str(self.path)),dict(id=2,method='live2d_info'),dict(id=3,method='live2d_edit',operation='parameter',index=0,value=0),dict(id=4,method='pick_layer',animation=0,u=.5,v=.5)]
        result=subprocess.run([sys.executable,str(home/'backend.py'),'--cache',str(Path(self.tmp.name)/'protocol')],input='\n'.join(json.dumps(r) for r in requests)+'\n',text=True,encoding='utf8',capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr[-1000:])
        replies=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['id'] for r in replies],[1,2,3,4]);self.assertTrue(all(r['ok'] for r in replies),replies)
        self.assertIsNotNone(replies[-1]['result']['selected'])

if __name__=='__main__':unittest.main()
