"""Bounded MOC3 field-table editing. Preserve topology and unknown sections.

Field layout references: OpenL2D moc3ingbird and MINT's documented section
layout. Only existing, validated records are patched; no pointer relocation.
"""
import math
import struct

import numpy as np


class Moc3:
    COUNTS = ('parts','deformers','warp','rotation','meshes','parameters','part_forms',
              'warp_forms','rotation_forms','mesh_forms','positions','binding_indices',
              'form_bindings','parameter_bindings','keys','uvs','indices','masks')
    SCHEMA = [
        ('parts',1,'id:s binding:i begin:i count:i visible:I enabled:I parent:i'),
        ('deformers',1,'id:s binding:i visible:I enabled:I part:i parent:i type:I specific:i'),
        ('warp',0,'binding:i begin:i count:i vertices:i rows:I columns:I'),
        ('rotation',0,'binding:i begin:i count:i base_angle:f'),
        ('meshes',4,'id:s binding:i begin:i count:i visible:I enabled:I part:i parent:i texture:I flags:B vertices:i uv:i index_begin:i index_count:i mask_begin:i mask_count:i'),
        ('parameters',1,'id:s maximum:f minimum:f default:f repeat:I decimals:I begin:i count:i'),
        ('part_forms',0,'order:f'), ('warp_forms',0,'opacity:f position:i'),
        ('rotation_forms',0,'opacity:f angle:f x:f y:f scale:f reflect_x:I reflect_y:I'),
        ('mesh_forms',0,'opacity:f order:f position:i'), ('positions',0,'value:f'),
        ('binding_indices',0,'value:i'), ('form_bindings',0,'begin:i count:i'),
        ('parameter_bindings',0,'begin:i count:i'), ('keys',0,'value:f'),
        ('uvs',0,'value:f'), ('indices',0,'value:H'), ('masks',0,'value:i')]

    def __init__(self, data):
        if not 72 <= len(data) <= 256*1024*1024 or data[:4] != b'MOC3' or data[4] not in {1,2,3,4,5}:
            raise ValueError('不支持此 MOC3 版本')
        self.data = bytearray(data)
        self.version = data[4]
        self.endian = '>' if data[5] else '<'
        counts, canvas = self.unpack('II',0x40)
        self.counts = dict(zip(self.COUNTS,self.unpack('18I',counts)))
        if any(n > 10_000_000 for n in self.counts.values()):
            raise ValueError('MOC3 数量超出上限')
        self.canvas = self.unpack('5f',canvas)
        if not all(math.isfinite(x) for x in self.canvas) or not 0 < self.canvas[0] <= 100000:
            raise ValueError('MOC3 画布无效')
        self.fields = {}
        slot = 2
        for section, skip, spec in self.SCHEMA:
            slot += skip
            for entry in spec.split():
                name, kind = entry.split(':')
                offset, = self.unpack('I',0x40+slot*4); slot += 1
                width = 64 if kind == 's' else struct.calcsize(kind)
                self.span(offset,self.counts[section]*width)
                self.fields[section,name] = offset,kind,width
        self.revision = 0

    def span(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError('MOC3 数据越界')
        return self.data[offset:offset+size]

    def unpack(self, fmt, offset):
        self.span(offset,struct.calcsize(self.endian+fmt))
        return struct.unpack_from(self.endian+fmt,self.data,offset)

    def values(self, section, field):
        offset,kind,width = self.fields[section,field]
        n = self.counts[section]
        if kind == 's':
            return [bytes(self.data[offset+i*64:offset+(i+1)*64]).split(b'\0',1)[0].decode('utf8','replace') for i in range(n)]
        return np.frombuffer(self.data,dtype=self.endian+kind,count=n,offset=offset).copy()

    def record(self, section, index):
        if not 0 <= index < self.counts.get(section,0):
            raise ValueError('请选择有效项目')
        return {name:(bytes(self.span(off+index*64,64)).split(b'\0',1)[0].decode('utf8','replace') if kind=='s'
                      else self.unpack(kind,off+index*width)[0])
                for (group,name),(off,kind,width) in self.fields.items() if group==section}

    def set(self, section, field, index, value):
        if (section,field) not in self.fields or not 0 <= index < self.counts[section]:
            raise ValueError('编辑位置无效')
        offset,kind,width = self.fields[section,field]
        if kind == 's':
            encoded = str(value).encode('utf8')
            if not encoded or len(encoded) >= 64 or b'\0' in encoded:
                raise ValueError('名称需为 1–63 字节')
            self.data[offset+index*64:offset+(index+1)*64] = encoded.ljust(64,b'\0')
        else:
            value = float(value) if kind == 'f' else int(value)
            if not math.isfinite(value) or abs(value) > 1e8:
                raise ValueError('数值超出范围')
            struct.pack_into(self.endian+kind,self.data,offset+index*width,value)
        self.revision += 1

    def points(self, section, index, form=0):
        row = self.record('meshes' if section=='uvs' else section,index)
        if section == 'meshes' or section == 'warp':
            if not 0 <= form < row['count']:
                raise ValueError('关键形编号无效')
            form_row = self.record('mesh_forms' if section == 'meshes' else 'warp_forms',row['begin']+form)
            start,n = form_row['position'],row['vertices']*2
            pool = self.values('positions','value')
            if start < 0 or start+n > len(pool): raise ValueError('关键形顶点范围无效')
            return pool[start:start+n].reshape(-1,2),start
        if section == 'uvs':
            mesh = self.record('meshes',index);start=mesh['uv'];n=mesh['vertices']*2
            pool=self.values('uvs','value')
            if start < 0 or start+n > len(pool): raise ValueError('UV 范围无效')
            return pool[start:start+n].reshape(-1,2),start
        raise ValueError('请选择网格或曲面变形器')

    def move_point(self, section, index, form, vertex, x, y):
        points,start = self.points(section,index,form)
        if not 0 <= vertex < len(points): raise ValueError('顶点编号无效')
        pool = 'uvs' if section == 'uvs' else 'positions'
        self.set(pool,'value',start+vertex*2,x);self.set(pool,'value',start+vertex*2+1,y)

    def transform(self, section,index,form,dx=0,dy=0,scale=1,angle=0):
        points,start=self.points(section,index,form)
        if not 0.01 <= scale <= 100 or not all(math.isfinite(v) for v in [dx,dy,scale,angle]):
            raise ValueError('变换数值无效')
        c,s=math.cos(math.radians(angle)),math.sin(math.radians(angle))
        center=points.mean(axis=0)
        result=(points-center) @ np.array([[c,s],[-s,c]])*scale+center+[dx,dy]
        pool='uvs' if section=='uvs' else 'positions'
        for i,value in enumerate(result.flatten()): self.set(pool,'value',start+i,float(value))

    def triangles(self,index):
        row=self.record('meshes',index)
        start,n=row['index_begin'],row['index_count']
        values=self.values('indices','value')
        if start<0 or n%3 or start+n>len(values):raise ValueError('三角形范围无效')
        return values[start:start+n].reshape(-1,3)

    def set_triangle(self,index,triangle,vertices):
        row=self.record('meshes',index);triangles=self.triangles(index)
        if not 0<=triangle<len(triangles) or len(vertices)!=3 or len(set(vertices))!=3 or any(not 0<=v<row['vertices'] for v in vertices):
            raise ValueError('三角形顶点无效')
        for i,value in enumerate(vertices):self.set('indices','value',row['index_begin']+triangle*3+i,value)

    def reparent(self,section,index,parent):
        if section not in {'meshes','deformers'}:raise ValueError('请选择网格或变形器')
        if not -1<=parent<self.counts['deformers']:raise ValueError('父变形器无效')
        if section=='deformers':
            seen={index};cursor=parent
            while cursor>=0:
                if cursor in seen:raise ValueError('层级不能循环')
                seen.add(cursor);cursor=self.record('deformers',cursor)['parent']
        self.set(section,'parent',index,parent)

    def form_keys(self,row,form):
        """Decode the mixed-radix parameter grid for an existing keyform."""
        source=row.get('binding',-1)
        if source<0:return []
        binding=self.record('form_bindings',source)
        pool=self.values('binding_indices','value');keys=self.values('keys','value');owners={}
        for i in range(self.counts['parameters']):
            p=self.record('parameters',i)
            for k in range(p['begin'],p['begin']+p['count']):owners[k]=(i,p['id'])
        result=[]
        for i in pool[binding['begin']:binding['begin']+binding['count']]:
            pb=self.record('parameter_bindings',int(i));count=pb['count']
            if not count or int(i) not in owners:continue
            index,name=owners[int(i)];offset=form%count;form//=count
            result.append(dict(index=index,key=name,value=float(keys[pb['begin']+offset])))
        return result
