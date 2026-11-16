"""Hardware 2D compositing through Qt's native Metal / Direct3D 11 backend.

Textures remain on the device. Colors are evaluated in shaders, and ordered
triangles share one dynamic vertex buffer. No visible Qt window is created.
"""
from array import array
from collections import OrderedDict
import ctypes
from pathlib import Path
import sys

import shiboken6
from PySide6 import QtGui as G
from PySide6.QtCore import QSize

from .render import qimage
from .scene import Scene


class GpuUnavailable(RuntimeError):
    pass


class GpuDevice:
    def __init__(self):
        if G.QGuiApplication.instance() is None:
            raise GpuUnavailable("图形环境尚未初始化")
        if sys.platform == "darwin":
            params, backend = G.QRhiMetalInitParams(), G.QRhi.Implementation.Metal
            library = Path(G.__file__).parent / "Qt/lib/QtGui.framework/Versions/A/QtGui"
            symbol = "_ZN4QRhi19beginOffscreenFrameEPP17QRhiCommandBuffer6QFlagsINS_14BeginFrameFlagEE"
        elif sys.platform == "win32":
            params, backend = G.QRhiD3D11InitParams(), G.QRhi.Implementation.D3D11
            library = Path(G.__file__).parent / "Qt6Gui.dll"
            symbol = "?beginOffscreenFrame@QRhi@@QEAA?AW4FrameOpResult@1@PEAPEAVQRhiCommandBuffer@@V?$QFlags@W4BeginFrameFlag@QRhi@@@@@Z"
        else:
            raise GpuUnavailable("当前系统没有已实现的 GPU 后端")
        self.rhi = G.QRhi.create(backend, params)
        if self.rhi is None:
            raise GpuUnavailable("无法创建硬件图形设备")
        info = self.rhi.driverInfo()
        if info.deviceType == G.QRhiDriverInfo.DeviceType.CpuDevice:
            shiboken6.delete(self.rhi)
            raise GpuUnavailable("当前图形设备为软件模拟")
        self.info = {"hardware": True, "api": "Metal" if sys.platform == "darwin" else "Direct3D 11",
                     "device": bytes(info.deviceName).decode("utf8", "replace")}
        # PySide 6.12 exposes QRhiCommandBuffer** as an input object. This narrow
        # ABI adapter restores the output pointer; all drawing uses public QRhi.
        self.library = ctypes.CDLL(str(library))
        self.begin = getattr(self.library, symbol)
        self.begin.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
        self.begin.restype = ctypes.c_int
        self.textures, self.bindings = OrderedDict(), {}
        self.texture_bytes = 0
        self.pending = []
        self.targets, self.target_textures, self.pipelines = [], [], {}
        self.mask_targets, self.mask_textures = [], []
        self.descriptor = self.vertex_buffer = None
        self.size, self.capacity = None, 0
        self.sampler = self.rhi.newSampler(G.QRhiSampler.Filter.Linear, G.QRhiSampler.Filter.Linear,
            G.QRhiSampler.Filter.None_, G.QRhiSampler.AddressMode.ClampToEdge, G.QRhiSampler.AddressMode.ClampToEdge)
        if not self.sampler.create():
            raise GpuUnavailable("无法创建 GPU 采样器")
        folder = Path(__file__).with_name("shaders")
        self.shaders = [G.QShader.fromSerialized((folder/name).read_bytes()) for name in ("scene.vert.qsb", "scene.frag.qsb")]
        if not all(shader.isValid() for shader in self.shaders):
            raise GpuUnavailable("GPU 着色器无效")
        image = G.QImage(1, 1, G.QImage.Format.Format_RGBA8888); image.fill(G.QColor("white"))
        self.white = self.rhi.newTexture(G.QRhiTexture.Format.RGBA8, QSize(1, 1))
        if not self.white.create():
            raise GpuUnavailable("无法创建 GPU 纹理")
        self.pending.append((self.white, image))
        self.upload_count = 0

    @staticmethod
    def delete(resource):
        if resource is not None and shiboken6.isValid(resource):
            shiboken6.delete(resource)

    def reset_textures(self):
        self.rhi.finish()
        for pipeline in self.pipelines.values(): self.delete(pipeline)
        self.pipelines.clear()
        for binding in self.bindings.values(): self.delete(binding)
        self.bindings.clear()
        for texture, size in self.textures.values(): self.delete(texture)
        self.textures.clear(); self.texture_bytes = 0
        self.pending = [(texture, image) for texture, image in self.pending if texture is self.white]

    def texture(self, package, name):
        if name is None:
            return self.white
        key = id(package), name
        if key in self.textures:
            self.textures.move_to_end(key)
            return self.textures[key][0]
        image = package.texture(name)
        if image is None:
            return None
        texture = self.rhi.newTexture(G.QRhiTexture.Format.RGBA8, QSize(*image.size))
        if not texture.create():
            self.delete(texture)
            raise GpuUnavailable("GPU 纹理分配失败")
        self.textures[key] = texture, image.width*image.height*4
        self.texture_bytes += image.width*image.height*4
        self.pending.append((texture, qimage(image)))
        return texture

    def binding(self, texture, destination=None, mask=None):
        destination = destination or self.white
        mask = mask or self.white
        key = tuple(shiboken6.getCppPointer(t)[0] for t in (texture,destination,mask))
        if key not in self.bindings:
            binding = self.rhi.newShaderResourceBindings()
            stage = G.QRhiShaderResourceBinding.StageFlag.FragmentStage
            binding.setBindings([G.QRhiShaderResourceBinding.sampledTexture(0, stage, texture, self.sampler),
                                 G.QRhiShaderResourceBinding.sampledTexture(1, stage, destination, self.sampler),
                                 G.QRhiShaderResourceBinding.sampledTexture(2, stage, mask, self.sampler)])
            if not binding.create():
                self.delete(binding)
                raise GpuUnavailable("GPU 纹理绑定失败")
            self.bindings[key] = binding
        return self.bindings[key]

    def release_targets(self):
        for pipeline in self.pipelines.values(): self.delete(pipeline)
        self.pipelines.clear()
        for binding in self.bindings.values(): self.delete(binding)
        self.bindings.clear()
        for target in self.targets: self.delete(target)
        self.targets.clear()
        for target in self.mask_targets: self.delete(target)
        self.mask_targets.clear()
        self.delete(self.descriptor); self.descriptor = None
        for texture in self.target_textures: self.delete(texture)
        self.target_textures.clear()
        for texture in self.mask_textures: self.delete(texture)
        self.mask_textures.clear()

    def prepare_masks(self, count, size):
        while len(self.mask_targets)<count:
            texture=self.rhi.newTexture(G.QRhiTexture.Format.BGRA8,QSize(*size),1,G.QRhiTexture.Flag.RenderTarget)
            if not texture.create():raise GpuUnavailable('GPU 遮罩纹理分配失败')
            self.mask_textures.append(texture)
            target=self.rhi.newTextureRenderTarget(G.QRhiTextureRenderTargetDescription(G.QRhiColorAttachment(texture)),
                G.QRhiTextureRenderTarget.Flag.PreserveColorContents)
            self.mask_targets.append(target);target.setRenderPassDescriptor(self.descriptor)
            if not target.create():raise GpuUnavailable('GPU 遮罩画布创建失败')

    def prepare(self, size, vertex_bytes):
        if self.size != tuple(size):
            self.rhi.finish(); self.release_targets()
            for _ in range(2):
                texture = self.rhi.newTexture(G.QRhiTexture.Format.BGRA8, QSize(*size), 1,
                    G.QRhiTexture.Flag.RenderTarget | G.QRhiTexture.Flag.UsedAsTransferSource)
                if not texture.create(): raise GpuUnavailable("GPU 画布分配失败")
                self.target_textures.append(texture)
                target = self.rhi.newTextureRenderTarget(G.QRhiTextureRenderTargetDescription(G.QRhiColorAttachment(texture)),
                    G.QRhiTextureRenderTarget.Flag.PreserveColorContents)
                self.targets.append(target)
                if self.descriptor is None: self.descriptor = target.newCompatibleRenderPassDescriptor()
                target.setRenderPassDescriptor(self.descriptor)
                if not target.create(): raise GpuUnavailable("GPU 画布创建失败")
            self.size = tuple(size)
        if vertex_bytes > self.capacity:
            self.rhi.finish(); self.delete(self.vertex_buffer)
            self.capacity = max(65536, 1 << (vertex_bytes-1).bit_length())
            self.vertex_buffer = self.rhi.newBuffer(G.QRhiBuffer.Type.Dynamic, G.QRhiBuffer.UsageFlag.VertexBuffer, self.capacity)
            if not self.vertex_buffer.create(): raise GpuUnavailable("GPU 顶点缓存分配失败")

    def pipeline(self, mode):
        if mode in self.pipelines: return self.pipelines[mode]
        pipeline = self.rhi.newGraphicsPipeline()
        pipeline.setShaderStages([G.QRhiShaderStage(G.QRhiShaderStage.Type.Vertex, self.shaders[0]),
                                  G.QRhiShaderStage(G.QRhiShaderStage.Type.Fragment, self.shaders[1])])
        layout = G.QRhiVertexInputLayout()
        layout.setBindings([G.QRhiVertexInputBinding(52)])
        F = G.QRhiVertexInputAttribute.Format
        layout.setAttributes([G.QRhiVertexInputAttribute(0,0,F.Float2,0), G.QRhiVertexInputAttribute(0,1,F.Float2,8),
                              G.QRhiVertexInputAttribute(0,2,F.Float4,16), G.QRhiVertexInputAttribute(0,3,F.Float4,32),
                              G.QRhiVertexInputAttribute(0,4,F.Float,48)])
        pipeline.setVertexInputLayout(layout)
        pipeline.setShaderResourceBindings(self.binding(self.white))
        blend = G.QRhiGraphicsPipeline.TargetBlend()
        blend.enable = mode not in {-1, 3}
        Factor = G.QRhiGraphicsPipeline.BlendFactor
        blend.srcColor = Factor.One; blend.srcAlpha = Factor.One
        blend.dstColor = Factor.One if mode == 8 else Factor.OneMinusSrcColor if mode == 4 else Factor.OneMinusSrcAlpha
        blend.dstAlpha = Factor.One if mode == 8 else Factor.OneMinusSrcAlpha
        if mode in {5,6}:  # Cubism multiply/add preserve destination alpha.
            blend.srcColor=Factor.DstColor if mode==5 else Factor.One
            blend.dstColor=Factor.OneMinusSrcAlpha if mode==5 else Factor.One
            blend.srcAlpha=Factor.Zero;blend.dstAlpha=Factor.One
        if mode in {9,10,11}:
            blend.srcColor=blend.dstColor=blend.srcAlpha=blend.dstAlpha=Factor.One
            blend.opColor={9:G.QRhiGraphicsPipeline.BlendOp.ReverseSubtract,10:G.QRhiGraphicsPipeline.BlendOp.Max,11:G.QRhiGraphicsPipeline.BlendOp.Min}[mode]
            blend.opAlpha=G.QRhiGraphicsPipeline.BlendOp.Max
        pipeline.setTargetBlends([blend]); pipeline.setRenderPassDescriptor(self.descriptor)
        if not pipeline.create():
            self.delete(pipeline)
            raise GpuUnavailable("GPU 绘制管线创建失败")
        self.pipelines[mode] = pipeline
        return pipeline

    def render(self, package, scene, frame, size=None):
        x0,x1,y0,y1 = getattr(scene,'rect',scene.animation.movie.rect)
        width,height=max(1,x1-x0),max(1,y1-y0)
        size=tuple(size or (width,height))
        if size[0]*size[1]>32*1024*1024: raise ValueError("动画画布过大")
        draws=scene.draws(frame)
        vertices=array('f')
        clear=((-1,1), (1,1), (-1,-1), (-1,-1), (1,1), (1,-1))
        for x,y in clear: vertices.extend((x,y,.5,.5,0,0,0,0,0,0,0,0,0))
        used=set()
        chains={}; visiting=set()
        def need_chain(chain):
            if not chain or chain in chains:return
            if chain in visiting or len(visiting)+len(chains)>=64:raise ValueError('动画遮罩组合超出限制')
            visiting.add(chain)
            entries=compile_draws(scene.masks.get(chain[-1],[]),chain[:-1],True)
            visiting.remove(chain);chains[chain]=entries
        def compile_draws(draws,prefix=(),is_mask=False):
            commands=[]
            for draw in draws:
                texture=self.texture(package,draw.texture)
                if texture is None:continue
                if draw.texture:used.add((id(package),draw.texture))
                mode=0 if is_mask else draw.blend
                if mode not in {0,1,2,3,4,5,6,8,9,10,11}:
                    scene.issues.add(f'未还原的混合模式：{mode}');mode=0
                chain=prefix+draw.masks;need_chain(chain)
                first=len(vertices)//13
                a,b,c,d,tx,ty=draw.matrix
                bits=float(mode+(16 if chain else 0)+(32 if draw.inverted_mask and chain else 0))
                if hasattr(draw.vertices,'dtype'):
                    import numpy as np
                    source=draw.vertices;packed=np.empty((len(source),13),dtype=np.float32)
                    packed[:,0]=(a*source[:,0]+c*source[:,1]+tx-x0)*2/width-1
                    packed[:,1]=1-(b*source[:,0]+d*source[:,1]+ty-y0)*2/height
                    packed[:,2:4]=source[:,2:4];packed[:,4:8]=draw.multiply;packed[:,8:12]=draw.add;packed[:,12]=bits
                    vertices.frombytes(packed.tobytes())
                else:
                    for x,y,u,v in draw.vertices:
                        px,py=a*x+c*y+tx,b*x+d*y+ty
                        vertices.extend(((px-x0)*2/width-1,1-(py-y0)*2/height,u,v,*draw.multiply,*draw.add,bits))
                count=len(vertices)//13-first
                if commands and commands[-1][0] is texture and commands[-1][1]==mode and commands[-1][4]==chain and mode!=3:
                    old=commands[-1];commands[-1]=(texture,mode,old[2],old[3]+count,chain)
                else:commands.append((texture,mode,first,count,chain))
            return commands
        commands=compile_draws(draws)
        data=vertices.tobytes(); self.prepare(size,len(data))
        self.prepare_masks(len(chains),size)
        mask_lookup={chain:self.mask_textures[index] for index,chain in enumerate(chains)}
        for _,mode,_,_,_ in commands: self.pipeline(mode)
        self.pipeline(0)
        self.pipeline(-1)
        updates=self.rhi.nextResourceUpdateBatch()
        updates.updateDynamicBuffer(self.vertex_buffer,0,data)
        for texture,image in self.pending:
            updates.uploadTexture(texture,image); self.upload_count+=1
        self.pending.clear()
        pointer=ctypes.c_void_p()
        if self.begin(shiboken6.getCppPointer(self.rhi)[0],ctypes.byref(pointer),0)!=0:
            updates.release(); raise GpuUnavailable("GPU 帧启动失败")
        cb=shiboken6.wrapInstance(pointer.value,G.QRhiCommandBuffer)
        current=0; active=False
        try:
            if chains:
                cb.resourceUpdate(updates);updates=None
            for index,(chain,entries) in enumerate(chains.items()):
                cb.beginPass(self.mask_targets[index],G.QColor(0,0,0,0),G.QRhiDepthStencilClearValue(1.,0));active=True
                cb.setGraphicsPipeline(self.pipeline(-1))
                cb.setViewport(G.QRhiViewport(0,0,*size));cb.setVertexInput(0,[(self.vertex_buffer,0)])
                cb.setShaderResources(self.binding(self.white));cb.draw(6)
                cb.setGraphicsPipeline(self.pipeline(0))
                for texture,mode,first,count,parent_chain in entries:
                    cb.setShaderResources(self.binding(texture,mask=mask_lookup.get(parent_chain)));cb.draw(count,1,first)
                cb.endPass();active=False
            cb.beginPass(self.targets[current],G.QColor(0,0,0,0),G.QRhiDepthStencilClearValue(1.,0),updates); active=True
            cb.setViewport(G.QRhiViewport(0,0,*size)); cb.setGraphicsPipeline(self.pipeline(-1))
            cb.setShaderResources(self.binding(self.white)); cb.setVertexInput(0,[(self.vertex_buffer,0)]); cb.draw(6)
            for texture,mode,first,count,chain in commands:
                destination=None
                if mode==3:
                    cb.endPass(); active=False
                    copy=self.rhi.nextResourceUpdateBatch()
                    copy.copyTexture(self.target_textures[1-current],self.target_textures[current])
                    cb.resourceUpdate(copy)
                    destination=self.target_textures[current]; current=1-current
                    cb.beginPass(self.targets[current],G.QColor(0,0,0,0),G.QRhiDepthStencilClearValue(1.,0)); active=True
                    cb.setGraphicsPipeline(self.pipeline(mode))
                    cb.setViewport(G.QRhiViewport(0,0,*size)); cb.setVertexInput(0,[(self.vertex_buffer,0)])
                cb.setGraphicsPipeline(self.pipeline(mode)); cb.setShaderResources(self.binding(texture,destination,mask_lookup.get(chain)))
                cb.draw(count,1,first)
            result=G.QRhiReadbackResult()
            read=self.rhi.nextResourceUpdateBatch(); read.readBackTexture(self.target_textures[current],result)
            cb.endPass(read); active=False
            if self.rhi.endOffscreenFrame()!=G.QRhi.FrameOpResult.FrameOpSuccess:
                raise GpuUnavailable("GPU 帧提交失败")
            pixels=memoryview(result.data)
            if len(pixels)!=size[0]*size[1]*4: raise GpuUnavailable("GPU 帧回读不完整")
            image=G.QImage(pixels,*size,size[0]*4,G.QImage.Format.Format_ARGB32_Premultiplied).copy()
        except BaseException:
            if active: cb.endPass()
            if self.rhi.isRecordingFrame(): self.rhi.endOffscreenFrame()
            raise
        # A large animation may temporarily pin more textures than the budget;
        # evict only textures not referenced by the frame that just completed.
        if self.texture_bytes>256*1024*1024:
            for key in list(self.textures):
                if self.texture_bytes<=256*1024*1024: break
                if key in used: continue
                texture,nbytes=self.textures.pop(key)
                pointer=shiboken6.getCppPointer(texture)[0]
                for binding_key in list(self.bindings):
                    if pointer in binding_key: self.delete(self.bindings.pop(binding_key))
                self.delete(texture); self.texture_bytes-=nbytes
        return image

    def close(self):
        if self.rhi is None: return
        self.rhi.finish(); self.release_targets(); self.reset_textures()
        self.delete(self.vertex_buffer); self.delete(self.white); self.delete(self.sampler)
        self.delete(self.rhi); self.rhi=None


class GpuSceneRenderer:
    @property
    def rect(self):return getattr(self.scene,'rect',self.animation.movie.rect)
    def close(self):
        self.scene.close()
        self.package = self.animation = None

    def __init__(self, package, animation, device):
        self.package, self.animation, self.device = package, animation, device
        self.scene=Scene(package,animation)
        self.issues=self.scene.issues

    def render(self, frame, size=None):
        return self.device.render(self.package,self.scene,frame,size)
