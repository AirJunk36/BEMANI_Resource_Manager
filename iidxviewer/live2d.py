"""Cubism evaluation + native Metal/D3D compositing; source files stay untouched."""
from collections import OrderedDict
import ctypes as C
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

from .moc3 import Moc3
from .resources import Asset, Package
from .scene import Draw
from .layers import LayerControls

_native = _core = None


@contextmanager
def quiet_native():
    """The SDK writes C stdio; keep it away from the JSON response pipe."""
    libc=C.CDLL('ucrtbase' if os.name=='nt' else None)
    libc.fflush.argtypes=[C.c_void_p];libc.fflush.restype=C.c_int
    libc.fflush(None);saved=os.dup(1)
    try:
        os.dup2(2,1);yield
    finally:
        libc.fflush(None);os.dup2(saved,1);os.close(saved)


@quiet_native()
def runtime():
    global _native,_core
    if _native is not None: return _native,_core
    spec=importlib.util.find_spec('live2d')
    if spec is None: raise ValueError('应用缺少 Live2D 核心')
    folder=Path(spec.origin).parent
    library=next(iter(folder.glob('_live2d*.so')),None) or next(iter(folder.glob('_live2d*.pyd')),None)
    if library is None: raise ValueError('Live2D 运行库不完整')
    loader=importlib.util.spec_from_file_location('_live2d',library)
    native=importlib.util.module_from_spec(loader);loader.loader.exec_module(native)
    native.enableLog(False);native.init_internal(str(folder))
    # Windows wheels hide the statically linked Core symbols. Use the official
    # C ABI DLL for geometry while the wrapper evaluates motion/physics.
    core_path=Path(__file__).parent/'vendor/live2d/Live2DCubismCore.dll' if os.name=='nt' else library
    core=C.CDLL(str(core_path))
    if not hasattr(core,'csmGetDrawableRenderOrders'):
        core.csmGetDrawableRenderOrders=core.csmGetRenderOrders
    def function(name,result,args):
        f=getattr(core,name);f.restype=result;f.argtypes=args;return f
    ptr=C.c_void_p;integer=C.c_int;u32=C.c_uint32;fp=C.POINTER(C.c_float)
    for name,result,args in [
        ('csmHasMocConsistency',integer,[ptr,u32]),('csmReviveMocInPlace',ptr,[ptr,u32]),
        ('csmGetSizeofModel',u32,[ptr]),('csmInitializeModelInPlace',ptr,[ptr,ptr,u32]),
        ('csmUpdateModel',None,[ptr]),('csmResetDrawableDynamicFlags',None,[ptr]),
        ('csmReadCanvasInfo',None,[ptr,fp,fp,fp]),
        ('csmGetParameterCount',integer,[ptr]),('csmGetParameterValues',fp,[ptr]),
        ('csmGetPartCount',integer,[ptr]),('csmGetPartIds',C.POINTER(C.c_char_p),[ptr]),('csmGetPartOpacities',fp,[ptr]),
        ('csmGetDrawableCount',integer,[ptr]),('csmGetDrawableIds',C.POINTER(C.c_char_p),[ptr]),
        ('csmGetDrawableConstantFlags',C.POINTER(C.c_ubyte),[ptr]),
        ('csmGetDrawableDynamicFlags',C.POINTER(C.c_ubyte),[ptr]),
        ('csmGetDrawableRenderOrders',C.POINTER(integer),[ptr]),
        ('csmGetDrawableTextureIndices',C.POINTER(integer),[ptr]),
        ('csmGetDrawableOpacities',fp,[ptr]),
        ('csmGetDrawableVertexCounts',C.POINTER(integer),[ptr]),
        ('csmGetDrawableVertexPositions',C.POINTER(fp),[ptr]),
        ('csmGetDrawableVertexUvs',C.POINTER(fp),[ptr]),
        ('csmGetDrawableIndexCounts',C.POINTER(integer),[ptr]),
        ('csmGetDrawableIndices',C.POINTER(C.POINTER(C.c_ushort)),[ptr]),
        ('csmGetDrawableMaskCounts',C.POINTER(integer),[ptr]),
        ('csmGetDrawableMasks',C.POINTER(C.POINTER(integer)),[ptr]),
        ('csmGetDrawableMultiplyColors',fp,[ptr]),('csmGetDrawableScreenColors',fp,[ptr])]:
        function(name,result,args)
    _native,_core=native,core
    return native,core


class CoreModel:
    @quiet_native()
    def __init__(self,data):
        _,self.api=runtime()
        self.moc_buffer=C.create_string_buffer(len(data)+64)
        address=(C.addressof(self.moc_buffer)+63)&~63
        C.memmove(address,bytes(data),len(data))
        if not self.api.csmHasMocConsistency(address,len(data)): raise ValueError('MOC3 一致性检查失败')
        moc=self.api.csmReviveMocInPlace(address,len(data))
        if not moc:raise ValueError('MOC3 无法加载')
        size=self.api.csmGetSizeofModel(moc)
        if not 0<size<=256*1024*1024:raise ValueError('Live2D 模型内存超出上限')
        self.model_buffer=C.create_string_buffer(size+16)
        self.model=self.api.csmInitializeModelInPlace(moc,(C.addressof(self.model_buffer)+15)&~15,size)
        if not self.model:raise ValueError('Live2D 模型初始化失败')
        self.count=self.api.csmGetDrawableCount(self.model)
        if not 0<self.count<=20000:raise ValueError('Live2D 网格数量无效')
        self.params=np.ctypeslib.as_array(self.api.csmGetParameterValues(self.model),shape=(self.api.csmGetParameterCount(self.model),))
        part_count=self.api.csmGetPartCount(self.model)
        self.part_ids=[self.api.csmGetPartIds(self.model)[i].decode() for i in range(part_count)]
        self.parts=np.ctypeslib.as_array(self.api.csmGetPartOpacities(self.model),shape=(part_count,))
        self.part_defaults=self.parts.copy()
        def array(name,n=None):return np.ctypeslib.as_array(getattr(self.api,'csmGetDrawable'+name)(self.model),shape=(n or self.count,))
        self.ids=[self.api.csmGetDrawableIds(self.model)[i].decode() for i in range(self.count)]
        self.orders=array('RenderOrders');self.flags=array('ConstantFlags');self.textures=array('TextureIndices')
        self.dynamic=array('DynamicFlags')
        self.visible=Moc3(data).values('meshes','visible')
        self.opacity=array('Opacities');self.vertex_counts=array('VertexCounts');self.index_counts=array('IndexCounts')
        self.positions=[np.ctypeslib.as_array(self.api.csmGetDrawableVertexPositions(self.model)[i],shape=(int(n)*2,)).reshape(-1,2) for i,n in enumerate(self.vertex_counts)]
        self.uvs=[np.ctypeslib.as_array(self.api.csmGetDrawableVertexUvs(self.model)[i],shape=(int(n)*2,)).reshape(-1,2).copy() for i,n in enumerate(self.vertex_counts)]
        for uv in self.uvs:uv[:,1]=1-uv[:,1]
        self.indices=[np.ctypeslib.as_array(self.api.csmGetDrawableIndices(self.model)[i],shape=(int(n),)).copy() for i,n in enumerate(self.index_counts)]
        counts=array('MaskCounts');ptrs=self.api.csmGetDrawableMasks(self.model)
        self.masks=[tuple(ptrs[i][j] for j in range(int(n))) for i,n in enumerate(counts)]
        self.multiply=array('MultiplyColors',self.count*4).reshape(-1,4)
        self.screen=array('ScreenColors',self.count*4).reshape(-1,4)
        canvas=(C.c_float*2)();origin=(C.c_float*2)();ppu=C.c_float()
        self.api.csmReadCanvasInfo(self.model,canvas,origin,C.byref(ppu))
        self.canvas=tuple(canvas);self.origin=tuple(origin);self.ppu=ppu.value

    def update(self,values,parts=None):
        self.params[:]=values
        self.parts[:]=self.part_defaults
        for name,value in (parts or {}).items():
            if name in self.part_ids:self.parts[self.part_ids.index(name)]=value
        self.api.csmResetDrawableDynamicFlags(self.model);self.api.csmUpdateModel(self.model)


@dataclass
class LiveAnimation:
    name:str
    movie:object
    document:object
    group:str=''
    motion:int=0
    length:int=1
    expression:str=''
    live2d:bool=True
    legacy:bool=True


class LiveDocument:
    def __init__(self,path):
        self.path=Path(path).resolve()
        if self.path.suffix.lower()=='.moc3':
            matches=[p for p in self.path.parent.glob('*.model3.json') if json.loads(p.read_text()).get('FileReferences',{}).get('Moc')==self.path.name]
            if not matches:raise ValueError('请选择配套的 model3.json')
            self.path=next((p for p in matches if p.name==self.path.stem+'.model3.json'),matches[0])
        self.root=self.path.parent.resolve();self.manifest=json.loads(self.path.read_text(encoding='utf-8-sig'))
        self.refs=self.manifest['FileReferences'];self.moc_path=self.reference(self.refs['Moc'])
        self.moc=Moc3(self.moc_path.read_bytes());self.core=CoreModel(self.moc.data)
        self.textures=[self.reference(p) for p in self.refs.get('Textures',[])]
        self.json_files={self.path.name:self.manifest}
        self.warnings=[]
        self.source_files={self.path,self.moc_path,*self.textures}
        def collect(value):
            if isinstance(value,str) and (value.lower().endswith('.json') or value.lower().endswith('.wav')):
                try:p=self.reference(value)
                except ValueError:
                    self.warnings.append('缺少关联文件：'+value);return
                self.source_files.add(p)
                if p.suffix=='.json':self.json_files[value]=json.loads(p.read_text(encoding='utf-8-sig'))
            elif isinstance(value,dict):
                for item in value.values():collect(item)
            elif isinstance(value,list):
                for item in value:collect(item)
        collect(self.refs)
        for key in ['Physics','Pose','DisplayInfo','UserData']:
            if self.refs.get(key) and self.refs[key] not in self.json_files:self.refs.pop(key)
        for group,entries in self.refs.get('Motions',{}).items():
            self.refs['Motions'][group]=[row for row in entries if row.get('File') in self.json_files]
        if 'Expressions' in self.refs:self.refs['Expressions']=[r for r in self.refs['Expressions'] if r.get('File') in self.json_files]
        self.values={};self.texture_overrides={};self.history=[];self.redo=[];self.revision=0
        self.changed_json={self.path.name} if self.warnings else set();self.motion_state=None;self.native=None;self.last_frame=-1
        self.curves={};self.opacity=1.
        self.workspace=tempfile.TemporaryDirectory(prefix='bemani-live2d-')
        self.staging=Path(self.workspace.name)
        for source in self.source_files:
            target=self.staging/source.relative_to(self.root);target.parent.mkdir(parents=True,exist_ok=True)
            # Textures are only read by our compositor, not the motion evaluator.
            if source not in self.textures:shutil.copyfile(source,target)
        self.last_edit=None;self.last_edit_at=0
        display=self.json_files.get(self.refs.get('DisplayInfo',''),{})
        self.names={row['Id']:row.get('Name',row['Id']) for section in ['Parameters','Parts'] for row in display.get(section,[])}
        self.reload_native()

    def reference(self,name):
        p=(self.root/name.replace('\\','/')).resolve()
        if not p.is_relative_to(self.root) or not p.is_file():raise ValueError('缺少或越界的 Live2D 关联文件：'+name)
        return p

    @quiet_native()
    def reload_native(self,stage=True):
        native,_=runtime()
        if stage:
            (self.staging/self.moc_path.relative_to(self.root)).write_bytes(self.moc.data)
            for name,document in self.json_files.items():
                path=self.staging/name;path.parent.mkdir(parents=True,exist_ok=True)
                # Framework's JSON parser requires whitespace after the root.
                path.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        model=native.Model();model.LoadModelJson(str(self.staging/self.path.name),False)
        model.SetAutoBlink(False);model.SetAutoBreath(False)
        self.native=model;self.parameter_ids=list(model.GetParamIds());self.motion_state=None;self.last_frame=-1

    def snapshot(self):
        return bytes(self.moc.data),dict(self.values),dict(self.texture_overrides),json.dumps(self.json_files,ensure_ascii=False),set(self.changed_json)

    def mutate(self,operation,rebuild=True,reload=False,merge=None):
        before=self.snapshot()
        try:
            operation();candidate=CoreModel(self.moc.data) if rebuild else self.core
            if reload:self.reload_native()
        except BaseException:
            self.restore(before);raise
        now=time.monotonic()
        if not merge or merge!=self.last_edit or now-self.last_edit_at>.45:self.history.append(before)
        self.last_edit,self.last_edit_at=merge,now
        self.history=self.history[-50:];self.redo.clear()
        while len(self.history)>1 and sum(len(s[0])+len(s[3]) for s in self.history)>64*1024*1024:self.history.pop(0)
        self.core=candidate;self.revision+=1

    def restore(self,snapshot):
        data,self.values,self.texture_overrides,documents,self.changed_json=snapshot
        self.moc=Moc3(data);self.json_files=json.loads(documents);self.core=CoreModel(data)
        self.manifest=self.json_files[self.path.name];self.refs=self.manifest['FileReferences']
        display=self.json_files.get(self.refs.get('DisplayInfo',''),{})
        self.names={row['Id']:row.get('Name',row['Id']) for section in ['Parameters','Parts'] for row in display.get(section,[])}
        self.curves.clear()
        self.reload_native();self.last_edit=None
        self.revision+=1;self.last_frame=-1

    def undo(self,redo=False):
        source,destination=(self.redo,self.history) if redo else (self.history,self.redo)
        if not source:return
        destination.append(self.snapshot());self.restore(source.pop())

    def evaluate(self,animation,frame):
        key=animation.name
        if self.motion_state!=key or frame<self.last_frame:
            if self.motion_state is not None:self.reload_native(stage=False)
            self.native.StopAllMotions();self.native.ResetAllParameters();self.native.SaveParameters()
            self.native.ResetExpressions()
            if animation.group:self.native.StartMotion(animation.group,animation.motion,3)
            if animation.expression:self.native.SetExpression(animation.expression)
            self.last_frame=0;self.motion_state=key
        delta=(frame-self.last_frame)/60
        if delta>0:
            steps=max(1,min(600,math.ceil(delta*60)))
            for _ in range(steps):self.update_native(delta/steps)
        elif self.last_frame==0:self.update_native(0)
        values=[self.native.GetParamValueByIndex(i) for i in range(len(self.parameter_ids))]
        for name,value in self.values.items():
            if name in self.parameter_ids:values[self.parameter_ids.index(name)]=value
        parts={};self.opacity=1.
        if animation.group:
            name=self.refs['Motions'][animation.group][animation.motion]['File'];motion=self.json_files[name]
            if name not in self.curves:
                from .live2d_motion import Curve
                self.curves[name]=[Curve.read(row) for row in motion.get('Curves',[]) if row['Target']=='PartOpacity' or row['Target']=='Model' and row['Id']=='Opacity']
            seconds=frame/60;duration=float(motion['Meta']['Duration'])
            if motion['Meta'].get('Loop') and duration>0:seconds%=duration
            for curve in self.curves[name]:
                value=max(0,min(1,curve.at(seconds)))
                if curve.target=='PartOpacity':parts[curve.name]=value
                else:self.opacity=value
        self.core.update(values,parts);self.last_frame=frame

    def update_native(self,delta):
        self.native.LoadParameters();self.native.UpdateMotion(delta);self.native.SaveParameters()
        self.native.UpdateExpression(delta)
        for key,value in self.values.items():self.native.SetParamById(key,value,1)
        self.native.UpdatePhysics(delta);self.native.UpdatePose(delta)

    def info(self):
        sections={}
        for section in ['meshes','deformers','warp','rotation','parts']:
            ids=self.moc.values(section,'id') if (section,'id') in self.moc.fields else []
            sections[section]=[{'id':i,'name':self.names.get(name,name)} for i,name in enumerate(ids)]
        parameters=[]
        ids=self.moc.values('parameters','id')
        for i,name in enumerate(ids):
            row=self.moc.record('parameters',i)
            parameters.append(dict(id=i,key=name,name=self.names.get(name,name),minimum=row['minimum'],maximum=row['maximum'],value=self.values.get(name,row['default']),default=row['default']))
        common={'ParamAngleX','ParamAngleY','ParamAngleZ','ParamBodyAngleX','ParamBodyAngleY','ParamBodyAngleZ','ParamEyeLOpen','ParamEyeROpen','ParamEyeBallX','ParamEyeBallY','ParamMouthOpenY','ParamMouthForm','ParamBreath','ParamEyeLSmile','ParamEyeRSmile'}
        for p in parameters:p['common']=p['key'] in common or p['key'].startswith(('PARAM_ANGLE','PARAM_EYE_OPEN','PARAM_MOUTH','PARAM_BODY_ANGLE','PARAM_BREATH'))
        return dict(version=self.moc.version,parameters=parameters,sections=sections,textures=[p.relative_to(self.root).as_posix() for p in self.textures],
                    documents=sorted(self.json_files),revision=self.revision,can_undo=bool(self.history),can_redo=bool(self.redo))

    def edit(self,request):
        kind=request['operation']
        if kind in {'undo','redo'}:self.undo(kind=='redo');return
        if kind=='release':
            self.values.clear();self.reload_native();self.revision+=1;return
        if kind=='pose':
            for key in request['keys']:
                row=self.moc.record('parameters',int(key['index']));value=float(key['value'])
                if not math.isfinite(value) or not row['minimum']<=value<=row['maximum']:raise ValueError('关键形参数无效')
                self.values[row['id']]=value
            self.revision+=1;return
        def apply():
            if kind=='parameter':
                index=int(request['index']);row=self.moc.record('parameters',index)
                value=float(request['value'])
                if not math.isfinite(value) or not row['minimum']<=value<=row['maximum']:raise ValueError('参数值超出范围')
                self.values[row['id']]=value
                if request.get('persist',True):self.moc.set('parameters','default',index,value)
            elif kind=='point':
                self.moc.move_point(request['section'],int(request['index']),int(request.get('form',0)),int(request['vertex']),float(request['x']),float(request['y']))
            elif kind=='transform':
                self.moc.transform(request['section'],int(request['index']),int(request.get('form',0)),*[float(request.get(k,d)) for k,d in [('dx',0),('dy',0),('scale',1),('angle',0)]])
            elif kind=='field':
                section,field=request['section'],request['field']
                allowed={'rotation_forms':{'opacity','angle','x','y','scale','reflect_x','reflect_y'},'mesh_forms':{'opacity','order'},'warp_forms':{'opacity'},
                         'parameters':{'minimum','maximum','default'},'meshes':{'visible','enabled','texture','flags'},'parts':{'visible','enabled'}}
                if field not in allowed.get(section,set()):raise ValueError('此结构字段不可直接修改')
                index=int(request['index']);value=float(request['value'])
                if field in {'opacity'} and not 0<=value<=1:raise ValueError('不透明度范围为 0–1')
                if field in {'visible','enabled','reflect_x','reflect_y'} and value not in {0,1}:raise ValueError('开关值需为 0 或 1')
                if section=='meshes' and field=='texture' and not 0<=value<len(self.textures):raise ValueError('贴图编号无效')
                if section=='meshes' and field=='flags' and not 0<=value<=15:raise ValueError('混合标记无效')
                if section=='rotation_forms' and field=='scale' and not 1e-9<=value<=100:raise ValueError('缩放需大于 0')
                self.moc.set(section,field,index,value)
                if section=='parameters':
                    row=self.moc.record(section,index)
                    if not row['minimum']<=row['default']<=row['maximum']:raise ValueError('默认值需位于参数范围内')
            elif kind=='parent':self.moc.reparent(request['section'],int(request['index']),int(request['parent']))
            elif kind=='triangle':self.moc.set_triangle(int(request['index']),int(request['triangle']),[int(v) for v in request['vertices']])
            elif kind=='json':
                name=request['document']
                if name not in self.json_files or name==self.path.name:raise ValueError('请选择动作、物理或显示配置')
                if len(request['text'])>20*1024*1024:raise ValueError('配置过大')
                value=json.loads(request['text'])
                if not isinstance(value,dict):raise ValueError('配置需为 JSON 对象')
                if name.endswith('.motion3.json'):
                    if not isinstance(value.get('Curves'),list) or not isinstance(value.get('Meta'),dict):raise ValueError('动作需包含 Meta 和 Curves')
                    duration=float(value['Meta'].get('Duration',0))
                    if not math.isfinite(duration) or not 0<duration<=3600:raise ValueError('动作时长无效')
                    segment_count=0;point_count=0
                    for curve in value['Curves']:
                        if curve.get('Target') not in {'Model','Parameter','PartOpacity'} or not isinstance(curve.get('Segments'),list):raise ValueError('动作曲线无效')
                        segments=curve['Segments']
                        if len(segments)<2 or len(segments)>100000 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in segments):raise ValueError('动作曲线数据无效')
                        cursor=2
                        previous=segments[0];point_count+=1
                        while cursor<len(segments):
                            tag=segments[cursor];size=7 if tag==1 else 3 if tag in {0,2,3} else 0
                            if not size or cursor+size>len(segments):raise ValueError('动作曲线段无效')
                            end=segments[cursor+size-2]
                            if end<previous:raise ValueError('关键帧时间需递增')
                            previous=end;segment_count+=1;point_count+=3 if tag==1 else 1
                            cursor+=size
                    meta=value['Meta'];meta.update(CurveCount=len(value['Curves']),TotalSegmentCount=segment_count,TotalPointCount=point_count)
                    events=value.get('UserData',[]);meta.update(UserDataCount=len(events),TotalUserDataSize=sum(len(str(e.get('Value','')).encode('utf8')) for e in events))
                elif name.endswith('.physics3.json'):
                    settings=value.get('PhysicsSettings');meta=value.get('Meta')
                    if not isinstance(settings,list) or not isinstance(meta,dict):raise ValueError('物理配置缺少 Meta 或 PhysicsSettings')
                    for row in settings:
                        if not all(isinstance(row.get(k),list) for k in ['Input','Output','Vertices']) or len(row['Vertices'])<2:raise ValueError('物理输入、输出或粒子无效')
                        for axis in ['Position','Angle']:
                            norm=row.get('Normalization',{}).get(axis,{})
                            if not all(isinstance(norm.get(k),(int,float)) for k in ['Minimum','Default','Maximum']) or not norm['Minimum']<=norm['Default']<=norm['Maximum']:raise ValueError('物理归一化范围无效')
                        for output in row['Output']:
                            if not 1<=output.get('VertexIndex',-1)<len(row['Vertices']):raise ValueError('物理输出粒子编号无效')
                        for group,reference in [('Input','Source'),('Output','Destination')]:
                            for item in row[group]:
                                ref=item.get(reference,{})
                                if ref.get('Target')!='Parameter' or not isinstance(ref.get('Id'),str) or item.get('Type') not in {'X','Y','Angle'} or not isinstance(item.get('Weight'),(int,float)):raise ValueError('物理参数绑定无效')
                        for vertex in row['Vertices']:
                            if not all(isinstance(vertex.get(k),(int,float)) for k in ['Mobility','Delay','Acceleration','Radius']) or not all(isinstance(vertex.get('Position',{}).get(k),(int,float)) for k in ['X','Y']):raise ValueError('物理粒子数据无效')
                    meta.update(PhysicsSettingCount=len(settings),TotalInputCount=sum(len(r['Input']) for r in settings),TotalOutputCount=sum(len(r['Output']) for r in settings),VertexCount=sum(len(r['Vertices']) for r in settings))
                # Reject nonfinite numbers in every configuration before native parsing.
                json.dumps(value,allow_nan=False)
                self.json_files[name]=value;self.changed_json.add(name)
                self.curves.pop(name,None)
                if name.endswith('.cdi3.json'):
                    self.names={row['Id']:row.get('Name',row['Id']) for section in ['Parameters','Parts'] for row in value.get(section,[])}
            elif kind=='texture':
                index=int(request['index'])
                if not 0<=index<len(self.textures):raise ValueError('贴图编号无效')
                with Image.open(request['path']) as im:
                    if im.width*im.height>64*1024*1024:raise ValueError('贴图过大')
                    im.load()
                target=self.staging/('replacement-'+str(time.time_ns())+'.png')
                with Image.open(request['path']) as im:im.convert('RGBA').save(target)
                self.texture_overrides[index]=target
            else:raise ValueError('未知编辑操作')
        self.mutate(apply,rebuild=kind not in {'parameter','json','texture'},reload=kind=='json' or (kind=='field' and request['section']=='parameters'),
                    merge=(kind,request.get('index')) if kind=='parameter' else None)

    def save(self,target):
        target=Path(target).resolve()
        if target.is_relative_to(self.root):raise ValueError('请选择游戏目录外的位置')
        if target.exists():raise FileExistsError('目标已存在，请更名')
        target.mkdir()
        try:
            for source in self.source_files:
                destination=target/source.relative_to(self.root);destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
            (target/self.moc_path.relative_to(self.root)).write_bytes(self.moc.data)
            for name in self.changed_json:(target/name).write_text(json.dumps(self.json_files[name],ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            for i,source in self.texture_overrides.items():
                destination=target/self.textures[i].relative_to(self.root)
                with Image.open(source) as im:im.convert('RGBA').save(destination)
            CoreModel((target/self.moc_path.relative_to(self.root)).read_bytes())
        except BaseException:
            shutil.rmtree(target,ignore_errors=True);raise
        return dict(path=str(target),model=str(target/self.path.name))


class LivePackage(Package):
    def texture(self,name):
        index=int(name.split('/')[-1]);doc=self.archive
        key=index,doc.revision
        if key not in self._image_cache:
            with Image.open(doc.texture_overrides.get(index,doc.textures[index])) as image:self._image_cache[key]=image.convert('RGBA')
        while len(self._image_cache)>max(2,len(doc.textures)):self._image_cache.popitem(last=False)
        return self._image_cache[key]
    def close(self):
        self._image_cache.clear()
        if self.archive:
            self.archive.native=None;self.archive.core=None;self.archive.workspace.cleanup()
        self.archive=None


def open_live2d(path):
    doc=LiveDocument(path);package=LivePackage(Path(path));package.archive=doc;package.format='live2d'
    package.warnings=doc.warnings
    package.source_files=sorted(doc.source_files)
    package.assets=[Asset(p.relative_to(doc.root).as_posix(),'图片' if p.suffix.lower()=='.png' else '文件',p,p.stat().st_size) for p in package.source_files]
    width,height=map(round,doc.core.canvas)
    movie=SimpleNamespace(rect=(0,width,0,height),fps=60,warnings=set())
    package.animations=[LiveAnimation('默认姿态',movie,doc)]
    for group,entries in doc.refs.get('Motions',{}).items():
        for i,row in enumerate(entries):
            motion=doc.json_files[row['File']];duration=float(motion.get('Meta',{}).get('Duration',1))
            package.animations.append(LiveAnimation(group+(f' {i+1}' if len(entries)>1 else ''),movie,doc,group,i,max(1,math.ceil(duration*60))))
    for row in doc.refs.get('Expressions',[]):package.animations.append(LiveAnimation('表情 · '+row['Name'],movie,doc,length=180,expression=row['Name']))
    return package


class LiveLayers(LayerControls):
    def load(self):
        if self.loaded:return
        for i,name in enumerate(self.package.archive.core.ids):self.add(f'root/mesh:{i}','root',name,'网格',1)
        self.loaded=True
    def discover(self,scene,frame):self.load()


class LiveScene:
    def __init__(self,package,animation):
        self.package=package;self.animation=animation;self.controls=None;self.playback=None;self.masks={};self.issues=set()
    def close(self):self.package=self.animation=self.controls=None;self.masks.clear()
    def texture(self,name):return self.package.texture(name)
    def draws(self,frame):
        doc=self.package.archive;doc.evaluate(self.animation,frame);core=doc.core
        width,height=core.canvas;ox,oy=core.origin;ppu=core.ppu
        draws=[]
        for i in range(core.count):
            positions=core.positions[i]
            vertices=np.concatenate((positions,core.uvs[i]),axis=1)[core.indices[i]]
            color=core.multiply[i];screen=core.screen[i]
            # Cubism screen tint is c + screen - c*screen.
            multiply=(float(color[0]*(1-screen[0])),float(color[1]*(1-screen[1])),float(color[2]*(1-screen[2])),float(core.opacity[i]*doc.opacity))
            draw=Draw(f'live2d/{int(core.textures[i])}',vertices,(ppu,0,0,-ppu,ox,height-oy),multiply,(float(screen[0]),float(screen[1]),float(screen[2]),0),
                      {0:0,1:6,2:5}.get(int(core.flags[i])&3,0),(),f'root/mesh:{i}',bool(core.flags[i]&8))
            draws.append(draw)
        self.masks={}
        result=[]
        for i in np.argsort(core.orders,kind='stable'):
            i=int(i);draw=draws[i]
            if core.masks[i]:
                from dataclasses import replace
                group=tuple(core.masks[i]);self.masks[group]=[replace(draws[j],masks=(),multiply=(1,1,1,1),add=(0,0,0,0),blend=0) for j in group]
                draw.masks=(group,)
            if core.opacity[i]>0 and core.visible[i] and (core.dynamic[i]&1) and (self.controls is None or self.controls.visible(draw.layer)):
                result.append(draw)
        return result
