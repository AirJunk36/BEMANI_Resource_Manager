"""Read-only PE inspection and playback-family detection. Never loads a game DLL."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import struct


class PE:
    def __init__(self, data):
        self.data=data
        if data[:2]!=b'MZ' or len(data)<64:raise ValueError('不是 Windows PE 文件')
        pe=self.u32(60)
        if self.read(pe,4)!=b'PE\0\0':raise ValueError('PE 头无效')
        machine,count=struct.unpack('<HH',self.read(pe+4,4))
        self.arch={0x14c:'x86',0x8664:'x64',0xaa64:'ARM64'}.get(machine,f'0x{machine:04x}')
        optional=pe+24;size=struct.unpack('<H',self.read(pe+20,2))[0]
        magic=struct.unpack('<H',self.read(optional,2))[0]
        if magic not in {0x10b,0x20b} or count>96:raise ValueError('PE 可选头无效')
        self.directory=optional+(112 if magic==0x20b else 96)
        self.sections=[]
        for index in range(count):
            start=optional+size+40*index
            name=self.read(start,8).split(b'\0')[0].decode('ascii','replace')
            virtual_size,address,raw_size,offset=struct.unpack('<IIII',self.read(start+8,16))
            self.sections.append((name,address,max(raw_size,virtual_size),offset,raw_size))
        self.exports={}
        export,size=struct.unpack('<II',self.read(self.directory,8))
        if export and size:
            fields=struct.unpack('<IIHHIIIIIII',self.read(self.offset(export),40))
            base,nfunc,nnames,functions,names,ordinals=fields[5:]
            if nfunc>100000 or nnames>100000:raise ValueError('PE 导出表过大')
            for index in range(nnames):
                ref=self.u32(self.offset(names)+index*4)
                ordinal=struct.unpack('<H',self.read(self.offset(ordinals)+index*2,2))[0]
                if ordinal>=nfunc:raise ValueError('PE 导出索引无效')
                self.exports[self.string(ref)]={'ordinal':base+ordinal,'rva':self.u32(self.offset(functions)+ordinal*4)}
        self.imports=[]
        import_rva,_=struct.unpack('<II',self.read(self.directory+8,8))
        if import_rva:
            at=self.offset(import_rva)
            for index in range(512):
                descriptor=struct.unpack('<IIIII',self.read(at+index*20,20))
                if not any(descriptor):break
                self.imports.append(self.string(descriptor[3]))
        self.version=None
        for name,address,length,offset,raw_size in self.sections:
            if name!='.rsrc':continue
            region=self.read(offset,raw_size)
            at=region.find(b'\xbd\x04\xef\xfe')
            if 0<=at<=len(region)-16:
                ms,ls=struct.unpack_from('<II',region,at+8)
                self.version='.'.join(str(v) for v in (ms>>16,ms&65535,ls>>16,ls&65535))

    def read(self, offset, size):
        if offset<0 or size<0 or offset+size>len(self.data):raise ValueError('PE 数据越界')
        return self.data[offset:offset+size]

    def u32(self,offset):return struct.unpack('<I',self.read(offset,4))[0]

    def offset(self,rva):
        for name,start,length,offset,raw_size in self.sections:
            if start<=rva<start+length:
                delta=rva-start
                if delta>=raw_size:raise ValueError('PE 地址没有文件数据')
                return offset+delta
        raise ValueError('PE 地址不属于有效分段')

    def string(self,rva):
        at=self.offset(rva);end=self.data.find(b'\0',at,min(len(self.data),at+4096))
        if end<0:raise ValueError('PE 字符串未终止')
        return self.data[at:end].decode('ascii','replace')


def family(version, package_format=None):
    if version is not None:
        if 9<=version<=17:return '9-17'
        if 18<=version<=19:return '18-19'
        if 20<=version<=24:return '20-24'
        if 25<=version<=26:return '25-26'
        if version>=27:return '27+'
    return '9-17' if package_format in {'legacy','gcz','catalog'} else '18-19' if package_format=='txp2' else 'AP2'


@lru_cache(maxsize=32)
def inspect(path, size, modified):
    path=Path(path)
    if size>64*1024*1024:raise ValueError('播放运行库过大')
    data=path.read_bytes();pe=PE(data)
    named='afp_boot' in pe.exports
    return {'path':str(path),'architecture':pe.arch,'version':pe.version,'sha256':hashlib.sha256(data).hexdigest(),
            'api_style':'named' if named else 'ordinal','export_count':len(pe.exports),
            'exports':pe.exports,'imports':pe.imports}


def detect(path, package_format=None):
    path=Path(path).resolve()
    version=None;library=None
    for part in path.parts:
        match=re.match(r'^(\d{1,2})(?:[_ -]|th$)',part,re.I)
        if match and 9<=int(match[1])<=99:version=int(match[1])
    roots=[path.parent,*list(path.parents)[1:9]]
    sdvx=any(re.search(r'sdvx|sound[ _-]*voltex',part,re.I) for part in path.parts)
    sdvx=sdvx or any((root/sub/'soundvoltex.dll').is_file() for root in roots for sub in ('','modules','contents/modules'))
    names=('afp-core.dll','libafp-win32.dll','libafp-win64.dll')
    if path.name.lower() in names:library=path
    for root in roots:
        if library:break
        for sub in ('','contents','contents/modules','modules'):
            folder=root/sub
            library=next((folder/name for name in names if (folder/name).is_file()),None)
            if library:break
    result={'family':family(version,package_format),'game_version':version,'library':None,
            'playback':'legacy-keyframes' if family(version,package_format)=='9-17' else 'portable-ap2-vm',
            'executes_game_code':False}
    if library:
        try:
            stat=library.stat();info=inspect(str(library),stat.st_size,stat.st_mtime_ns)
            result['library']={k:v for k,v in info.items() if k not in {'exports','imports'}}
            if version is None and info['api_style']=='ordinal':
                prefixes={name[:8] for name in info['exports']}
                result['family']='27+' if any(name.startswith('XCd229cc') for name in prefixes) else '25-26'
        except (OSError,ValueError,struct.error) as error:result['warning']='运行库检测失败：'+str(error)
    if sdvx:result['family']='27+';result['playback']='portable-ap2-vm'
    return result
