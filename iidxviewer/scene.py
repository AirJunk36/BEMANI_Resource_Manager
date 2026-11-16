"""Prepare ordered triangles; GPU resources and pixel operations live elsewhere."""
from dataclasses import dataclass, replace
import math

from .afp import display_list, read_shape
from .playback import Playback

IDENTITY = (1., 0., 0., 1., 0., 0.)
# AFP's enum differs from Cubism's internal modes 5/6.
AFP_BLENDS = {0:0,3:3,4:8,5:10,6:11,8:8,9:9,13:0,0x46:9,0x4f:8}


def compose(parent, child):
    a, b, c, d, x, y = parent
    e, f, g, h, u, v = child
    return a*e+c*f, b*e+d*f, a*g+c*h, b*g+d*h, a*u+c*v+x, b*u+d*v+y


@dataclass
class Draw:
    texture: str | None
    vertices: tuple
    matrix: tuple = IDENTITY
    multiply: tuple = (1., 1., 1., 1.)
    add: tuple = (0., 0., 0., 0.)
    blend: int = 0
    masks: tuple = ()
    layer: str = ''
    inverted_mask: bool = False


def clip_polygon(vertices, polygon):
    """Clip XY/UV vertices against an affine rectangle in world coordinates."""
    area=sum(x*v-y*u for (x,y),(u,v) in zip(polygon,polygon[1:]+polygon[:1]))
    if abs(area)<1e-8:return []
    sign=1 if area>0 else -1
    for (x,y),(u,v) in zip(polygon,polygon[1:]+polygon[:1]):
        def distance(point):return sign*((u-x)*(point[1]-y)-(v-y)*(point[0]-x))
        output=[]
        for previous,current in zip(vertices[-1:]+vertices[:-1],vertices):
            d0,d1=distance(previous),distance(current)
            if (d0>=0)!=(d1>=0):
                t=d0/(d0-d1)
                output.append(tuple(a+(b-a)*t for a,b in zip(previous,current)))
            if d1>=0:output.append(current)
        vertices=output
        if not vertices:break
    return vertices


class Scene:
    def close(self):
        if self.gauge_scene:self.gauge_scene.close();self.gauge_scene=None
        if self.actor and self.owns_actor:self.actor.close()
        self.actor=None
        if self.playback:
            self.playback.close()
        self.playback = self.package = self.animation = self.controls = None
        self.shapes.clear(); self.geometry.clear(); self.sizes.clear(); self.atlases.clear(); self.masks.clear();self.rect_masks.clear()

    def __init__(self, package, animation):
        self.package, self.animation = package, animation
        self.issues = set(animation.movie.warnings)
        self.shapes, self.geometry = {}, {}
        self.sizes = {}
        self.masks = {}
        self.rect_masks={}
        self.next_mask = 1
        self.playback = None if getattr(animation,"legacy",False) else Playback(package,animation,self.issues)
        from .afp_hosts import canvas,military_actor,motion_clip
        self.rect=canvas(package,animation)
        self.actor_index=military_actor(package) if motion_clip(animation.name) else None
        self.actor=None
        self.owns_actor=True
        self.host_frame=None
        self.gauge_scene=None
        self.show_host_gauge=self.actor_index is not None
        self.suppress_host_gauge=False
        self.controls = None
        self.atlases = dict(package.atlas_regions)
        for asset in package.assets:
            if asset.kind == "图集" and hasattr(asset.source, "images"):
                for sprite in asset.source.images:
                    self.atlases[sprite.name.removesuffix(".png")] = (asset.source.img_size, (sprite.imgrect[0], sprite.imgrect[2]))

    def texture(self, name):
        image = self.package.texture(name)
        if image is None:
            self.issues.add("缺少纹理：" + name)
        return image

    def size(self, name):
        if name not in self.sizes:
            image = self.texture(name)
            self.sizes[name] = image.size if image is not None else None
        return self.sizes[name]

    def shape(self, name):
        shape_name = name
        if name in self.geometry:
            return self.geometry[name]
        asset = self.package.files.get("geo/" + name)
        if asset is None:
            self.issues.add("缺少几何：" + name)
            return []
        magic = self.package.files.get("magic")
        shape = read_shape(asset.read(), ">" if magic and magic.read() == b"NGPF" else "<")
        draws = []
        for kind, flags, tex, color, indices in shape.primitives:
            if kind == 4:
                triangles = [indices[i:i+3] for i in range(0, len(indices)-2, 3)]
            elif kind == 5:
                triangles = [indices[i:i+3] for i in range(len(indices)-2)]
            elif kind == 6:
                triangles = [(indices[0], indices[i], indices[i+1]) for i in range(1, len(indices)-1)]
            else:
                self.issues.add(f"未还原的几何图元：{kind}")
                continue
            name = shape.names[tex] if tex < len(shape.names) and shape.uvs else None
            image = self.texture(name) if name else None
            if name and image is None:
                continue
            atlas, origin = self.atlases.get(name, (image.size if image else (1, 1), (0, 0)))
            vertices = []
            for tri in triangles:
                if name and any(i >= len(shape.uvs) for i in tri):
                    self.issues.add("几何 UV 索引不完整")
                    continue
                for i in tri:
                    x, y = shape.vertices[i]
                    u, v = shape.uvs[i] if name else (.5, .5)
                    vertices.append((x, y, (u*atlas[0]-origin[0])/image.width if name else .5,
                                     (v*atlas[1]-origin[1])/image.height if name else .5))
            draws.append((name, tuple(vertices), tuple(c/255 for c in color)))
        self.geometry[shape_name] = draws
        return draws

    def timeline(self, clip, matrix, multiply, add, blend, depth, output, inherited_masks=(), digit=None):
        if depth > 32:
            self.issues.add("动画递归超出限制")
            return
        if clip.rect_mask is not None:
            x0,x1,y0,y1=clip.rect_mask
            a,b,c,d,tx,ty=matrix
            polygon=tuple((a*x+c*y+tx,b*x+d*y+ty) for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)])
            mask_id=-len(self.rect_masks)-1
            self.rect_masks[mask_id]=polygon
            inherited_masks=inherited_masks+(mask_id,)
        active_masks=[]
        for child_depth, child in sorted(clip.children.items()):
            active_masks=[item for item in active_masks if item[0]>=child_depth]
            obj = child.render_spec()
            if obj is None:continue
            mask_end=obj.get('clip',0)
            is_mask=mask_end>child_depth
            masks=inherited_masks+tuple(mask for end,mask in active_masks)
            target=output
            if is_mask:
                if self.next_mask>32:
                    self.issues.add('动画遮罩数量超出限制');continue
                mask_id=self.next_mask;self.next_mask+=1
                target=self.masks[mask_id]=[]
                masks=()
            if self.controls and child.layer_key in self.controls.hidden:
                if is_mask:
                    active_masks.append((mask_end, mask_id))
                continue
            definition = child.definition
            sx, sy = obj.get("scale", (1, 1)); b, c = obj.get("skew", (0, 0))
            x, y = obj.get("position", (0, 0)); ox, oy = obj.get("origin", (0, 0))
            transform = compose(matrix, (sx, b, c, sy, x-sx*ox-c*oy, y-b*ox-sy*oy))
            own_mul, own_add = obj.get("multiply", (1, 1, 1, 1)), obj.get("add", (0, 0, 0, 0))
            mul = tuple(a*b for a, b in zip(multiply, own_mul))
            plus = tuple(a*b+c for a, b, c in zip(own_add, multiply, add))
            mode = obj.get("blend", blend)
            if mode not in AFP_BLENDS:self.issues.add(f'未识别的 AFP 混合模式：{mode}')
            kind, data = definition
            number=getattr(child,'hp_digit',digit)
            if child.name=='body' and self.actor_index is not None:
                self.draw_actor(child,transform,mul,plus,masks,target)
            elif kind == "sprite":
                self.timeline(child, transform, mul, plus, mode, depth+1, target, masks,number)
            elif kind == "image":
                size = self.size(data)
                if size:
                    w, h = size
                    vertices = ((0,0,0,0),(w,0,1,0),(0,h,0,1),(0,h,0,1),(w,0,1,0),(w,h,1,1))
                    target.append(Draw(data, vertices, transform, mul, plus, AFP_BLENDS.get(mode,0), masks, child.layer_key))
            else:
                for name, vertices, color in self.shape(data):
                    if number is not None and name=='hp_num0':name=f'hp_num{number}'
                    from .layers import texture_key
                    if self.controls and self.controls.hidden and name:
                        if texture_key(child.layer_key, name) in self.controls.hidden:
                            continue
                    tint = mul if name else tuple(a*b for a,b in zip(mul,color))
                    target.append(Draw(name, vertices, transform, tint, plus, AFP_BLENDS.get(mode,0), masks,
                                       texture_key(child.layer_key, name) if name else child.layer_key))
            if is_mask:active_masks.append((mask_end,mask_id))

    def draw_actor(self, marker, matrix, multiply, add, masks, output):
        # The game hides the m00..m10 'body' marker (virtual texture 100) and
        # copies its transform/colour onto the separate, continuously running 01.
        if self.actor is None:self.actor=Scene(self.package,self.package.animations[self.actor_index])
        frame=self.host_frame if self.host_frame is not None else self.render_frame
        if getattr(self.actor.animation,'legacy',False):frame%=self.actor.animation.length
        draws=self.actor.draws(frame)
        remap={key:self.next_mask+i for i,key in enumerate(self.actor.masks)}
        self.next_mask+=len(remap)
        if self.next_mask>33:
            self.issues.add('动画遮罩数量超出限制');return
        def mapped(draw, matte=False):
            mul=draw.multiply if matte else tuple(a*b for a,b in zip(multiply,draw.multiply))
            plus=draw.add if matte else tuple(a*b+c for a,b,c in zip(draw.add,multiply,add))
            return replace(draw,matrix=compose(matrix,draw.matrix),multiply=mul,add=plus,
                           masks=tuple(remap[key] for key in draw.masks) if matte else masks+tuple(remap[key] for key in draw.masks),layer=marker.layer_key)
        for key,entries in self.actor.masks.items():self.masks[remap[key]]=[mapped(draw,True) for draw in entries]
        output.extend(mapped(draw) for draw in draws)
        self.issues.update(self.actor.issues)

    def draws(self, frame):
        result = []
        self.render_frame=frame
        self.masks={};self.rect_masks={};self.next_mask=1
        if getattr(self.animation, "legacy", False):
            hidden = self.controls.hidden if self.controls else frozenset()
            if 'root' in hidden:
                return result
            if hasattr(self.animation,'draw_commands'):
                for layer,(name,(w,h),matrix,alpha,blend) in self.animation.draw_commands(frame,hidden):
                    if name=='@host/100' and self.actor_index is not None:
                        from types import SimpleNamespace
                        self.draw_actor(SimpleNamespace(layer_key=layer),matrix,(1,1,1,alpha),(0,0,0,0),(),result);continue
                    if name.startswith('@host/'):
                        ident=int(name.rsplit('/',1)[1]);fraction=max(0,min(100,getattr(self,'gauge',100)))/100
                        if 4001<=ident<=4008:
                            value=round(fraction*1000) if ident<=4004 else 1000
                            power=4004-ident if ident<=4004 else 4008-ident
                            name=f'HP_{value//10**power%10}'
                            image=self.texture(name)
                            if image is None:continue
                            w,h=image.size
                        elif ident==4009:
                            name='HP_GAUGE';image=self.texture(name)
                            if image is None:continue
                            w,h=image.size;a,b,c,d,x,y=matrix;matrix=(a*fraction,b*fraction,c,d,x,y)
                        else:continue
                    if self.size(name) is None:continue
                    vertices=((0,0,0,0),(w,0,1,0),(0,h,0,1),(0,h,0,1),(w,0,1,0),(w,h,1,1))
                    result.append(Draw(name,vertices,matrix,(1,1,1,alpha),blend=8 if blend=='add' else 9 if blend=='subtract' else 0,layer=layer))
                return self.host_gauge(result,frame)
            for layer, node in self.animation.draw_layers(frame, hidden):
                name, x, y, w, h, pivot, angle, alpha, blend = node
                if self.size(name) is None:
                    continue
                rad=math.radians(angle); c,s=math.cos(rad),math.sin(rad); px,py=pivot
                transform=(c,s,-s,c,px-c*px+s*py,py-s*px-c*py)
                vertices=((x,y,0,0),(x+w,y,1,0),(x,y+h,0,1),(x,y+h,0,1),(x+w,y,1,0),(x+w,y+h,1,1))
                result.append(Draw(name,vertices,transform,(1,1,1,alpha),blend=8 if blend=="add" else 9 if blend=="subtract" else 0,layer=layer))
        else:
            root=self.playback.frame(frame)
            if hasattr(self,'gauge'):
                def update(clip):
                    if clip.name=='gauge_hp':clip.variables['_xscale']=self.gauge
                    elif clip.name.startswith(('hp_num','hpmax_num')):
                        digits=clip.name.rsplit('num',1)[-1]
                        if digits.isdecimal() and int(digits)>0:
                            hp=10000 if clip.name.startswith('hpmax') else round(100*self.gauge)
                            clip.hp_digit=hp//int(digits)%10
                    for child in list(clip.children.values()):update(child)
                update(root)
            if self.controls and 'root' in self.controls.hidden:
                return result
            spec=root.render_spec()
            if spec is not None:
                sx,sy=spec.get('scale',(1,1));b,c=spec.get('skew',(0,0));x,y=spec.get('position',(0,0))
                self.timeline(root,(sx,b,c,sy,x,y),spec.get('multiply',(1,1,1,1)),spec.get('add',(0,0,0,0)),0,0,result)
        if self.rect_masks:
            result=self.clip_rectangles(result)
            self.masks={key:self.clip_rectangles(entries) for key,entries in self.masks.items()}
        return self.host_gauge(result,frame)

    def host_gauge(self, draws, frame):
        if not self.show_host_gauge or self.suppress_host_gauge:return draws
        if self.controls and not self.controls.visible('root/host-hp'):return draws
        animation=next((a for a in self.package.animations if a.name.casefold()=='hp' and '/' not in a.name),None)
        if animation is not None:
            if self.gauge_scene is None:self.gauge_scene=Scene(self.package,animation)
            self.gauge_scene.gauge=getattr(self,'gauge',100)
            draws.extend(replace(draw,layer='root/host-hp') for draw in self.gauge_scene.draws(frame%max(1,animation.length)))
        else:
            from .afp_hosts import gauge_draws
            draws.extend(gauge_draws(self.rect,getattr(self,'gauge',100)))
        return draws

    def clip_rectangles(self, draws):
        # AEP compositions use many independent rect masks. Geometric clipping
        # avoids allocating a full-size GPU texture for each of those bounds.
        result=[]
        for draw in draws:
            rectangles=[self.rect_masks[key] for key in draw.masks if key<0]
            if not rectangles:
                result.append(draw);continue
            a,b,c,d,tx,ty=draw.matrix
            vertices=[(a*x+c*y+tx,b*x+d*y+ty,u,v) for x,y,u,v in draw.vertices]
            clipped=[]
            for start in range(0,len(vertices)-2,3):
                polygon=vertices[start:start+3]
                for rect in rectangles:
                    polygon=clip_polygon(polygon,rect)
                    if not polygon:break
                for i in range(1,len(polygon)-1):clipped.extend((polygon[0],polygon[i],polygon[i+1]))
            if clipped:result.append(replace(draw,vertices=tuple(clipped),matrix=IDENTITY,masks=tuple(key for key in draw.masks if key>0)))
        return result
