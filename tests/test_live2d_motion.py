import unittest
from iidxviewer.live2d_motion import Curve

class CurveTests(unittest.TestCase):
    def curve(self,segments):return Curve.read(dict(Target='PartOpacity',Id='Part',Segments=segments))
    def test_linear_step_inverse_and_bezier(self):
        self.assertEqual(self.curve([0,0,0,1,1]).at(.5),.5)
        self.assertEqual(self.curve([0,0,2,1,1]).at(.5),0)
        self.assertEqual(self.curve([0,0,3,1,1]).at(.5),1)
        self.assertAlmostEqual(self.curve([0,0,1,.3,0,.7,1,1,1]).at(.5),.5,places=5)
        self.assertEqual(self.curve([0,.2,0,1,.8]).at(10),.8)
    def test_native_blend_preserves_alpha(self):
        from types import SimpleNamespace
        from collections import OrderedDict
        import numpy as np
        from PIL import Image
        from PySide6.QtGui import QGuiApplication
        from iidxviewer.scene import Draw
        from iidxviewer.render import SceneRenderer
        from iidxviewer.gpu import GpuDevice
        self.app=QGuiApplication.instance() or QGuiApplication([])
        vertices=((0,0,0,0),(4,0,1,0),(0,4,0,1),(0,4,0,1),(4,0,1,0),(4,4,1,1))
        animation=SimpleNamespace(movie=SimpleNamespace(rect=(0,4,0,4)))
        package=SimpleNamespace(texture=lambda _:Image.new('RGBA',(2,2),'white'))
        scene=SimpleNamespace(animation=animation,masks={},issues=set())
        cpu=object.__new__(SceneRenderer);cpu.scene=scene;cpu.animation=animation;cpu.package=package;cpu.images=OrderedDict();cpu.image_bytes=0
        gpu=GpuDevice()
        try:
            for mode,expected in [(5,[0,0,64,128]),(6,[128,0,128,128])]:
                scene.draws=lambda _,mode=mode:[Draw('white',vertices,multiply=(1,0,0,.5)),Draw('white',vertices,multiply=(0,0,1,.5),blend=mode)]
                for output in [cpu.render(0),gpu.render(package,scene,0)]:
                    pixels=np.frombuffer(output.constBits(),np.uint8).reshape(4,4,4)
                    np.testing.assert_allclose(pixels[2,1],expected,atol=2)
        finally:gpu.close()
