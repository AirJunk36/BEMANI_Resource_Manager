"""Stateful movie instances, independent child clocks, and deterministic seeking."""
import math
import random

from .actions import CONSTANTS, Function, Program, Ref, UNDEFINED, VM, number, text, truth
from .afp import apply_placement, display_list

METHODS = frozenset({'play','stop','gotoAndPlay','gotoAndStop','nextFrame','prevFrame','getInstanceAtDepth',
                    'getChildAt','getChildByName','getDepth','removeMovieClip','removeChild','duplicateMovieClip',
                    'attachMovie','createEmptyMovieClip','swapDepths','getBounds','getRect','localToGlobal','globalToLocal',
                    'deepPlay','deepStop','deepGotoAndPlay','deepGotoAndStop'})
BUILTINS = METHODS | {'Math','Object','Array','MovieClip','Color','String','Boolean','Number','int','parseInt','parseFloat',
                       'isNaN','isFinite','getTimer','random','randomNumber','eval','trace','undefined','NaN','Infinity',
                       'afp_prop_init','afp_prop_dummy','escape','unescape','escapeMultiByte','unescapeMultiByte',
                       'aeplib','aplib','asdlib','eManager','XMLController'}


class Clip:
    def __init__(self, engine, definition, parent=None, spec=None):
        self.engine, self.definition, self.parent = engine, definition, parent
        self.spec=dict(spec or {})
        from .layers import layer_key
        self.layer_key = layer_key(parent.layer_key, self.spec.get('depth', 0), self.spec.get('character', -1)) if parent else 'root'
        self.timeline=definition[1] if definition[0]=='sprite' else None
        self.frame=0;self.playing=True;self.pending=None;self.variables={};self.children={}
        self.created=engine.tick
        self.loaded=False
        self.depth=(parent.depth+1) if parent else 0
        name=self.spec.get('name','')
        self.name=engine.movie.string(name) if isinstance(name,int) and hasattr(engine.movie,'string') else str(name)
        self.events=self.spec.get('events',[])
        self.frame_gate=None;self.rect_mask=None;self.set_frame_value=None

    @property
    def total(self):return max(1,len(self.timeline.frames)) if self.timeline else 1

    def goto(self, target, play):
        if not self.timeline:return
        if isinstance(target,str):
            frames=[f for f,name in self.timeline.labels if name==target]
            if not frames:
                self.engine.issues.add('缺少动画标签：'+target);return
            frame=frames[0]
        else:
            n=number(target)
            if not math.isfinite(n):return
            frame=int(n)-1
        self.pending=max(0,min(self.total-1,frame));self.playing=play

    def load(self, frame, initial=False):
        for _ in range(16):
            self.pending=None
            self.apply_frame(frame)
            if self.pending is None or self.pending==self.frame:
                self.pending=None;return
            frame=self.pending
        self.pending=None;self.engine.issues.add('动画帧跳转超出限制')

    def apply_frame(self, frame):
        if not self.timeline:return
        sequential=self.loaded and frame==self.frame+1
        self.frame=frame
        self.loaded=True
        first,count=self.timeline.frames[frame] if self.timeline.frames else (0,0)
        tags=self.timeline.tags[first:first+count]
        if sequential:
            self.apply_tags(tags,frame)
            for code,value in tags:
                if code==122:self.engine.execute(value,self)
            return
        state=display_list(self.timeline,frame)
        children={}
        first,count=self.timeline.frames[frame] if self.timeline.frames else (0,0)
        removed={value for code,value in self.timeline.tags[first:first+count] if code==128}
        replacements={value['depth'] for code,value in self.timeline.tags[first:first+count] if code==127 and value['flags']&2 and not value['flags']&1}
        for depth,child in self.children.items():
            if getattr(child,'dynamic',False) and depth not in state and depth not in removed:children[depth]=child
        for depth,spec in state.items():
            old=self.children.get(depth)
            definition=self.engine.movie.definitions.get(spec.get('character'))
            if definition is None:
                self.engine.issues.add(f"缺少角色定义：{spec.get('character','?')}");continue
            if self.depth>=32:
                self.engine.issues.add('动画递归超出限制');continue
            if old and depth not in replacements and old.spec.get('character')==spec.get('character') and old.spec.get('start')==spec.get('start'):
                old.spec=dict(spec);old.events=spec.get('events',old.events);children[depth]=old
            else:
                child=Clip(self.engine,definition,self,spec);children[depth]=child
        for depth,child in self.children.items():
            if children.get(depth) is not child:self.engine.events(child,4)
        self.children=children
        for child in list(children.values()):
            if child.created==self.engine.tick and not getattr(child,'initialized',False):
                child.initialized=True;child.load(0,True);self.engine.events(child,1)
        first,count=self.timeline.frames[frame] if self.timeline.frames else (0,0)
        for code,value in self.timeline.tags[first:first+count]:
            if code==122:self.engine.execute(value,self)

    def apply_tags(self, tags, frame):
        # Replaying a cumulative snapshot each tick resurrects removed clips and
        # silently undoes swapDepths. Only this frame's authored tags may mutate
        # the running display list; snapshots are used for jumps/initialization.
        for code,value in tags:
            if code==128:
                old=self.children.pop(value,None)
                if old:self.engine.events(old,4)
            elif code==127:
                depth=value['depth'];old=self.children.get(depth)
                if value['flags']&1 and old is None:continue
                spec=apply_placement(old.spec if old else {},value,frame)
                definition=self.engine.movie.definitions.get(spec.get('character'))
                if definition is None:
                    self.engine.issues.add(f"缺少角色定义：{spec.get('character','?')}");continue
                if self.depth>=32:
                    self.engine.issues.add('动画递归超出限制');continue
                if old and value['flags']&1 and old.spec.get('character')==spec.get('character'):
                    old.spec=spec;old.events=spec.get('events',old.events)
                    if value['flags']&4:
                        for name in ['_x','_y','_xscale','_yscale','_rotation','_width','_height']:old.variables.pop(name,None)
                    if value['flags']&8:old.variables.pop('_alpha',None)
                else:
                    if old:self.engine.events(old,4)
                    child=Clip(self.engine,definition,self,spec)
                    self.children[depth]=child;child.initialized=True;child.load(0,True);self.engine.events(child,1)

    def advance(self):
        if self.timeline:
            if self.pending is not None:
                target,self.pending=self.pending,None;self.load(target)
            elif self.playing and self.total>1:self.load((self.frame+1)%self.total)
        self.engine.events(self,2)
        handler=self.variables.get('onEnterFrame')
        if handler is not None:self.engine.call(handler,self,[],self)
        for child in list(self.children.values()):
            if child.created<self.engine.tick:child.advance()

    def get(self, name):
        if name in self.variables:return self.variables[name]
        x,y=self.spec.get('position',(0,0));sx,sy=self.spec.get('scale',(1,1))
        if name in {'this','_root','_parent','_global'}:
            return {'this':self,'_root':self.engine.root,'_parent':self.parent or self,'_global':self.engine.globals}[name]
        values={'_x':x,'_y':y,'_xscale':sx*100,'_yscale':sy*100,'_currentframe':self.frame+1,
                '_totalframes':self.total,'_framesloaded':self.total,'_alpha':self.spec.get('multiply',(1,1,1,1))[3]*100,
                '_visible':True,'_name':self.name,'_rotation':0,'numChildren':len(self.children)}
        if name in values:return values[name]
        if name in {'_width','_height'}:
            return self.engine.dimensions(self)[0 if name=='_width' else 1]*abs(sx if name=='_width' else sy)
        for child in self.children.values():
            if child.name==name:return child
        return Ref('method',name) if name in METHODS else UNDEFINED

    def render_spec(self):
        if self.frame_gate is not None and self.frame<self.frame_gate:return None
        out=dict(self.spec)
        if not truth(self.variables.get('_visible',True)):return None
        x,y=out.get('position',(0,0));sx,sy=out.get('scale',(1,1));b,c=out.get('skew',(0,0))
        if '_x' in self.variables:x=number(self.variables['_x'])
        if '_y' in self.variables:y=number(self.variables['_y'])
        if '_xscale' in self.variables:sx=number(self.variables['_xscale'])/100
        if '_yscale' in self.variables:sy=number(self.variables['_yscale'])/100
        if '_rotation' in self.variables:
            angle=math.radians(number(self.variables['_rotation']));co,si=math.cos(angle),math.sin(angle)
            sx,b,c,sy=co*sx,si*sx,-si*sy,co*sy
        mul=out.get('multiply',(1,1,1,1))
        if '_alpha' in self.variables:mul=(*mul[:3],max(0,min(1,number(self.variables['_alpha'])/100)))
        if '_width' in self.variables or '_height' in self.variables:
            width,height=self.engine.dimensions(self)
            if '_width' in self.variables and width:sx=number(self.variables['_width'])/width
            if '_height' in self.variables and height:sy=number(self.variables['_height'])/height
        if not all(math.isfinite(v) and abs(v)<1e8 for v in (x,y,sx,sy,b,c,*mul)):return None
        out.update(position=(x,y),scale=(sx,sy),skew=(b,c),multiply=mul)
        return out


class Playback:
    def __init__(self, package, animation, issues):
        self.package,self.animation,self.movie,self.issues=package,animation,animation.movie,issues
        self.programs={};self.bounds={};self.vm=VM(self);self.tick=-1;self.budget=20000
        self.root=None;self.reset()

    def close(self):
        # Clips and the VM refer back to the engine. Drop heavy resources now,
        # rather than retaining old packages until a full cyclic GC pass.
        self.package = self.animation = self.movie = None
        self.root = self.vm = None
        self.globals.clear(); self.programs.clear(); self.bounds.clear()

    def reset(self):
        self.tick=0;self.budget=20000;self.random=random.Random(0)
        self.globals={}
        self.root=Clip(self,('sprite',self.animation.timeline))
        self.root.initialized=True;self.root.load(0,True)
        self.settle(self.root)

    def settle(self,clip):
        if clip.pending is not None:
            frame,clip.pending=clip.pending,None;clip.load(frame)
        for child in list(clip.children.values()):self.settle(child)

    def execute(self, data, clip):
        try:
            if data not in self.programs:
                self.programs[data]=Program(data,getattr(self.movie,'string',lambda ref:str(ref)))
            self.vm.execute(self.programs[data],clip)
        except (ValueError,TypeError,IndexError,KeyError,OverflowError,RecursionError) as error:
            self.issues.add('素材脚本：'+str(error))

    def events(self, clip, flag):
        for trigger,data in clip.events:
            if trigger&flag:self.execute(data,clip)

    def frame(self, target):
        if target<self.tick:self.reset()
        if target-self.tick>100000:raise ValueError('动画定位跨度过大')
        while self.tick<target:
            self.tick+=1;self.budget=20000;self.root.advance()
            self.settle(self.root)
        return self.root

    def target(self, clip, value):
        if isinstance(value,Clip):return value
        if value is None or value is UNDEFINED or isinstance(value,str) and value=='':return clip
        path=text(value).replace(':','/').replace('.','/')
        current=self.root if path.startswith('/') or path.startswith('_root') else clip
        for part in path.split('/'):
            if not part or part in {'this','_root'}:continue
            if part in {'_parent','..'}:current=current.parent or current;continue
            current=next((c for c in current.children.values() if c.name==part),None)
            if current is None:return UNDEFINED
        return current

    def variable(self, clip, name):
        if name in clip.variables:return clip.variables[name]
        if name in {'this','_root','_parent','_global'}:return clip.get(name)
        if name in self.globals:return self.globals[name]
        if ':' in name or '.' in name:
            prefix,_,key=name.replace(':','.').rpartition('.')
            obj=self.target(clip,prefix)
            if isinstance(obj,Clip):return self.get_member(obj,key,clip)
        for child in clip.children.values():
            if child.name==name:return child
        if name.startswith('_') and name in CONSTANTS.values():return clip.get(name)
        # A built-in reference is inert until explicitly called by the bytecode.
        return Ref('builtin',name) if name in BUILTINS or name.startswith('AFP常量') else UNDEFINED

    def set_variable(self, clip, name, value):
        if ':' in name or '.' in name:
            prefix,_,key=name.replace(':','.').rpartition('.')
            obj=self.target(clip,prefix)
            if isinstance(obj,Clip):self.set_member(obj,key,value,clip);return
        clip.variables[name]=value

    def get_member(self, obj, name, clip):
        if isinstance(obj,str) and obj=='':obj=clip
        if isinstance(obj,Clip):return obj.get(name)
        if isinstance(obj,dict):return obj.get(name,UNDEFINED)
        if isinstance(obj,(list,tuple,str)):
            if name=='length':return len(obj)
            if name.isdigit():return obj[int(name)] if int(name)<len(obj) else UNDEFINED
            return Ref('member',(obj,name))
        if isinstance(obj,Ref):
            if obj.value=='Math' and name in {'PI','E','LN2','LN10','SQRT2'}:
                return {'PI':math.pi,'E':math.e,'LN2':math.log(2),'LN10':math.log(10),'SQRT2':math.sqrt(2)}[name]
            return Ref('builtin',str(obj.value)+'.'+name)
        return UNDEFINED

    def set_member(self, obj, name, value, clip):
        if isinstance(obj,str):obj=self.target(clip,obj)
        if isinstance(obj,Clip):
            if name=='_name':obj.name=text(value)
            obj.variables[name]=value
        elif isinstance(obj,dict):obj[name]=value
        elif isinstance(obj,list) and name.isdigit() and int(name)<4096:
            index=int(name)
            while len(obj)<=index:obj.append(UNDEFINED)
            obj[index]=value

    def delete_member(self,obj,name):
        if isinstance(obj,Clip):obj.variables.pop(name,None)
        elif isinstance(obj,dict):obj.pop(name,None)

    def member_names(self,obj):
        return list(obj.variables) if isinstance(obj,Clip) else list(obj) if isinstance(obj,dict) else list(range(len(obj))) if isinstance(obj,list) else []

    def remove(self,clip):
        if isinstance(clip,Clip) and clip.parent:
            for depth,child in list(clip.parent.children.items()):
                if child is clip:self.events(clip,4);del clip.parent.children[depth]

    def clone(self,clip,name,depth):
        if isinstance(clip,Clip) and clip.parent:
            if len(clip.parent.children)>=4096:raise ValueError('动态动画实例数量超限')
            created=Clip(self,clip.definition,clip.parent,dict(clip.spec, depth=depth+16384));created.name=name
            created.dynamic=True
            created.frame_gate=clip.frame_gate;created.rect_mask=clip.rect_mask;created.set_frame_value=clip.set_frame_value
            created.variables=dict(clip.variables);clip.parent.children[depth+16384]=created;created.load(0,True)
            return created
        return UNDEFINED

    def dimensions(self,clip):
        kind,data=clip.definition
        key=kind,id(data) if kind=='sprite' else data
        if key not in self.bounds:
            if kind=='image':
                image=self.package.texture(data);size=image.size if image else (0,0)
            elif kind=='sprite':
                x0,x1,y0,y1=self.movie.rect;size=x1-x0,y1-y0
            else:
                from .afp import read_shape
                asset=self.package.files.get('geo/'+data)
                magic=self.package.files.get('magic')
                shape=read_shape(asset.read(),'>' if magic and magic.read()==b'NGPF' else '<') if asset else None
                points=shape.vertices if shape else []
                size=(max(x for x,y in points)-min(x for x,y in points),max(y for x,y in points)-min(y for x,y in points)) if points else (0,0)
            self.bounds[key]=size
        return self.bounds[key]

    def call(self, fn, receiver, args, clip, construct=False):
        if isinstance(fn,Function):
            try:return self.vm.execute(fn.program,receiver if isinstance(receiver,Clip) else fn.owner or clip,args,fn)
            except (ValueError,TypeError,OverflowError) as error:self.issues.add('素材函数：'+str(error));return UNDEFINED
        if not isinstance(fn,Ref):return UNDEFINED
        name=text(fn.value)
        obj=receiver if isinstance(receiver,Clip) else clip
        first=args[0] if args else UNDEFINED
        if fn.kind=='member':
            receiver,name=fn.value
            if isinstance(receiver,list):
                if name=='push':receiver.extend(args);return len(receiver)
                if name=='pop':return receiver.pop() if receiver else UNDEFINED
                if name=='shift':return receiver.pop(0) if receiver else UNDEFINED
                if name=='unshift':receiver[:0]=args;return len(receiver)
                if name=='join':return (text(first) if args else ',').join(text(v) for v in receiver)
                if name=='reverse':receiver.reverse();return receiver
                if name=='concat':return receiver+list(args)
            if isinstance(receiver,str):
                if name in {'toString','valueOf'}:return receiver
                if name=='split':return receiver.split(text(first)) if first is not UNDEFINED else [receiver]
                if name=='toLowerCase':return receiver.lower()
                if name=='toUpperCase':return receiver.upper()
                if name in {'substr','substring','slice'}:
                    start=int(number(first)) if args else 0
                    end=(start+int(number(args[1]))) if name=='substr' and len(args)>1 else int(number(args[1])) if len(args)>1 else len(receiver)
                    return receiver[start:end]
                if name=='charAt':return receiver[int(number(first)):int(number(first))+1]
                if name=='charCodeAt':return ord(receiver[int(number(first))]) if 0<=number(first)<len(receiver) else float('nan')
                if name=='indexOf':return receiver.find(text(first),int(number(args[1])) if len(args)>1 else 0)
            return UNDEFINED
        name=name.split('.')[-1]
        if name in {'aep_set_frame_control','aep_set_rect_mask','aep_set_set_frame'}:
            if not isinstance(first,Clip):return UNDEFINED
            if name=='aep_set_frame_control' and len(args)==3 and all(math.isfinite(number(v)) for v in args[1:]):
                child=first.children.get(int(number(args[1])))
                if child:child.frame_gate=int(number(args[2]))-1
            elif name=='aep_set_rect_mask' and len(args)==5 and all(math.isfinite(number(v)) for v in args[1:]):
                first.rect_mask=tuple(number(v) for v in args[1:])
            elif name=='aep_set_set_frame' and len(args)==2:
                first.set_frame_value=number(args[1])
            return UNDEFINED
        if isinstance(receiver,Ref) and receiver.value in {'aeplib','aplib'} and name in {'play','stop','deepPlay','deepStop','gotoAndPlay','gotoAndStop','deepGotoAndPlay','deepGotoAndStop'}:
            seek='Goto' in name or name.startswith('goto')
            if len(args)==(2 if seek else 1) and isinstance(first,Clip):
                playing=name.endswith('Play') or name=='play'
                def seek_children(target):
                    # Resolve a label on the requested composition once. Child
                    # compositions have independent labels and placement times.
                    for child in list(target.children.values()):
                        local=max(0,target.frame-child.spec.get('start',0))
                        child.goto(local+1,playing);self.settle(child);seek_children(child)
                def apply(target):
                    if seek:
                        target.goto(args[1],playing)
                        if name.startswith('deep') and target.pending is not None:
                            self.settle(target);seek_children(target)
                    else:target.playing=playing
                    if name.startswith('deep') and not seek:
                        for child in list(target.children.values()):apply(child)
                apply(first)
            return UNDEFINED
        if name in {'play','stop'}:obj.playing=name=='play';return UNDEFINED
        if name in {'gotoAndPlay','gotoAndStop'}:obj.goto(first,name=='gotoAndPlay');return UNDEFINED
        if name in {'nextFrame','prevFrame'}:obj.goto(obj.frame+(2 if name=='nextFrame' else 0),False);return UNDEFINED
        if name in {'getInstanceAtDepth','getChildAt'}:
            depth=int(number(first)) if math.isfinite(number(first)) else 0
            if name=='getChildAt':
                ordered=sorted(obj.children)
                return obj.children[ordered[depth]] if 0<=depth<len(ordered) else UNDEFINED
            return obj.children.get(depth+16384,UNDEFINED)
        if name in {'getChildByName','eval','getTarget'}:return self.target(obj,first)
        if name=='getDepth':return next((depth-16384 for depth,c in (obj.parent.children.items() if obj.parent else []) if c is obj),0)
        if name=='swapDepths' and obj.parent:
            old=next((depth for depth,c in obj.parent.children.items() if c is obj),None)
            target=next((depth for depth,c in obj.parent.children.items() if c is first),None) if isinstance(first,Clip) else int(number(first))+16384
            if old is not None and target is not None:
                other=obj.parent.children.pop(target,None);obj.parent.children[target]=obj
                if other is not None:obj.parent.children[old]=other
                elif old!=target:obj.parent.children.pop(old,None)
                if old!=target:
                    obj.dynamic=True
                    if other is not None:other.dynamic=True
            return UNDEFINED
        if name in {'removeMovieClip','removeChild'}:self.remove(first if isinstance(first,Clip) else obj);return UNDEFINED
        if name in {'duplicateMovieClip','cloneSprite'}:return self.clone(obj,text(first),int(number(args[1])) if len(args)>1 else 0)
        if name in {'attachMovie','createEmptyMovieClip'}:
            exported=next((cid for cid,lib in self.movie.exports if lib==first),None)
            definition=self.movie.definitions.get(exported) if name=='attachMovie' else ('sprite',type(self.movie.root)([],[]))
            if definition is not None:
                if len(obj.children)>=4096:raise ValueError('动态动画实例数量超限')
                depth=int(number(args[2])) if len(args)>2 else int(number(args[1])) if len(args)>1 else 0
                created=Clip(self,definition,obj,dict(depth=depth+16384,character=exported if exported is not None else -1));created.name=text(args[1] if name=='attachMovie' and len(args)>1 else first)
                created.dynamic=True;obj.children[depth+16384]=created;created.load(0,True)
                if len(args)>3 and isinstance(args[3],dict):created.variables.update(args[3])
                return created
            self.issues.add('缺少导出符号：'+text(first));return UNDEFINED
        if name in {'afp_prop_init','afp_prop_dummy','updateAfterEvent','trace'}:return UNDEFINED
        if name in {'Number','parseFloat','int','parseInt'}:
            value=number(first)
            return int(value) if name in {'int','parseInt'} and math.isfinite(value) else value
        if name=='String':return text(first) if args else ''
        if name=='Boolean':return truth(first)
        if name=='Object':return {}
        if name=='Array':return [UNDEFINED]*max(0,min(4096,int(first))) if len(args)==1 and isinstance(first,(int,float)) else list(args)
        if name=='isNaN':return math.isnan(number(first))
        if name=='isFinite':return math.isfinite(number(first))
        if name=='getTimer':return self.tick*1000/60
        if name in {'random','randomNumber'}:return self.random.random() if not args else int(self.random.random()*number(first))
        functions={'abs':abs,'floor':math.floor,'ceil':math.ceil,'round':lambda v:math.floor(v+.5),'sqrt':math.sqrt,
                   'sin':math.sin,'cos':math.cos,'tan':math.tan,'atan':math.atan,'atan2':math.atan2,'pow':pow,'min':min,'max':max}
        if name in functions:
            try:return functions[name](*[number(v) for v in args])
            except (ValueError,OverflowError,TypeError):return float('nan')
        if name in {'unescape','unescapeMultiByte','escape','escapeMultiByte'}:
            from urllib.parse import quote,unquote
            return unquote(text(first)) if name.startswith('unescape') else quote(text(first))
        self.issues.add('未提供游戏宿主函数：'+name)
        return UNDEFINED
