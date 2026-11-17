"""Read indexed song, sound-bank and BGA/AFP metadata without loading the DLL."""
from functools import lru_cache
from pathlib import Path
import re
import struct

VARIATIONS = {0: 'SPH', 1: 'SPN', 2: 'SPA', 3: 'SPB', 4: 'SPL',
              5: 'DPB', 6: 'DPH', 7: 'DPN', 8: 'DPA', 10: 'DPL'}
LEGACY_IDS={728:320,796:388,828:420}


@lru_cache(maxsize=1)
def eout_key():
    # Legacy EOUT subtractive stream; format reference: tau/crackeout.
    result=[]
    for seed in bytes.fromhex('55379fcce3a77d99ddaabbcffc674317'):
        state=[0]*55;state[54]=seed;value=1
        for i in range(54):
            index=(21*i+20)%55;state[index]=value;value=(seed-value)&255;seed=state[index]
        for i in range(220):state[i%55]=(state[i%55]-state[(i+31)%55])&255
        result.extend(state[:16])
    return bytes(result)


def decode_eout(data):
    key=eout_key()
    if len(data)<16 or bytes((v-key[i])&255 for i,v in enumerate(data[:4]))!=b'IIDX':return None
    import numpy as np
    return (np.frombuffer(data,np.uint8)-np.resize(np.frombuffer(key,np.uint8),len(data))).tobytes()


def id_offset(stride,version):
    return LEGACY_IDS.get(stride,1660 if stride==2040 else 944 if version>=27 else 456)


def data_root(original):
    return next((p for p in Path(original).resolve().parents if p.name.lower() == 'data'), None)


def file_id(original):
    path = Path(original)
    value = path.parent.name if re.fullmatch(r'\d{4,6}',path.parent.name) else path.stem
    match = re.fullmatch(r'(\d{4,6})(?:_pre)?', value)
    return match[1] if match else ''


def song_id(original):
    value=file_id(original)
    return int(value) if value else None


def canonical_id(number, legacy=False):
    return number//100*1000+number%100 if legacy else number


def song_key(original):
    value=file_id(original)
    return canonical_id(int(value),len(value)==4) if value else None


@lru_cache(maxsize=4)
def database(path, size, stamp):
    data = Path(path).read_bytes()
    if len(data)>32*1024*1024:raise ValueError('曲库过大')
    if data[:4] != b'IIDX' and len(data) >= 64:
        key = b'subnimix'
        decoded = bytes(v ^ key[i % 8] for i, v in enumerate(data[:64])) + data[64:]
        if decoded[:4]==b'IIDX':data=decoded
        elif (legacy:=decode_eout(data)) is not None:data=legacy
        else:
            from .legacy_codecs import decrypt_cbc_cts
            data=decrypt_cbc_cts(data,Path(path).stem)
    if data[:4] != b'IIDX' or len(data) < 16:
        raise ValueError('曲库格式无效')
    version = struct.unpack_from('<I', data, 4)[0]
    count = struct.unpack_from('<H', data, 8)[0]
    wide = 32<=version<256 and version != 80
    indices = struct.unpack_from('<I' if wide else '<H', data, 12 if wide else 10)[0]
    start = 16 + indices * (4 if wide else 2)
    expected = 2040 if wide else 1324 if 27 <= version <= 31 else 836 if version == 26 else 832 if 22 <= version <= 25 else 800 if 20 <= version <= 21 else 0
    # Resort Anthem and the CN Lincle table use four-digit IDs and 828-byte
    # records. The latter carries version 20 despite retaining the old layout.
    if (version<=20 or version==indices) and indices<=3000 and count:
        actual=(len(data)-start)//count
        if actual in LEGACY_IDS and start+count*actual==len(data):
            expected=actual
            if version>255:version=12
    if not expected or not count or start + count * expected != len(data):
        raise ValueError('此曲库布局尚未确认')
    return data, version, indices, start, expected


def entry(path, mid):
    path = Path(path); stat = path.stat()
    return table_entry(str(path),mid,*database(str(path),stat.st_size,stat.st_mtime_ns))


def table_entry(path,mid,data,version,count,start,stride):
    wide = stride == 2040
    if not 0 <= mid < count:
        return None
    index = struct.unpack_from('<I' if wide else '<H', data, 16 + mid * (4 if wide else 2))[0]
    at = start + index * stride
    if at + stride > len(data):
        return None
    legacy = stride in LEGACY_IDS
    id_at = id_offset(stride,version)
    if struct.unpack_from('<I', data, at + id_at)[0] != mid:
        return None
    names = ['SPB','SPN','SPH','SPA','SPL','DPB','DPN','DPH','DPA','DPL'] if version >= 27 else ['SPN','SPH','SPA','DPN','DPH','DPA','SPB','DPB']
    ident_at = at + id_at + 8
    delay_at = ident_at + len(names)
    movie_at = delay_at + (2 if version >= 27 else 4)
    def string(position, length=32):
        return data[position:position + length].split(b'\0')[0].decode('cp932', 'replace')
    title_data = data[at:at + (256 if wide else 64)]
    title = title_data.decode('utf-16le' if wide else 'cp932', 'replace').split('\0')[0]
    ascii_at = at + (256 if wide else 64)
    artist = data[at+448:at+576].decode('utf-16le','replace').split('\0')[0] if wide else '' if legacy else string(at+192,64)
    afp_at=movie_at+(100 if legacy else 36)
    return dict(song_id=mid, id_text=f'{mid:04}' if legacy else f'{mid:05}', legacy=legacy,
                title=title, title_ascii='' if legacy else string(ascii_at,64),artist=artist, version=version, database=str(path),
                idents=dict(zip(names, (chr(v) for v in data[ident_at:delay_at]))),
                bga=string(movie_at), bga_offset=struct.unpack_from('<h', data, delay_at)[0] / 60,
                afp_flag=struct.unpack_from('<I', data, afp_at-4)[0],
                afp=[string(afp_at+i*32) for i in range(10 if version>=22 else 9)])


@lru_cache(maxsize=4)
def dll_formats(path, size, stamp):
    data = Path(path).read_bytes()
    result = []
    for needle in [b'/data/info/0/music_data.bin', b'/data/info/music_data.bin', b'%s.wmv', b'%s.mp4']:
        if needle in data:
            result.append(needle.decode())
    return result


def metadata(original):
    root, mid = data_root(original), song_id(original)
    if root is None or mid is None:
        return None
    found = []
    for folder in [root/'info/0', root/'info/1', root/'info']:
        for name in ['music_data.bin', 'music_omni.bin','eout.bin']:
            path = folder/name
            if path.is_file():
                try:
                    item = entry(path, mid)
                    if item: found.append(item)
                except (ValueError, struct.error):
                    pass
    if not found:
        return None
    result = max(found, key=lambda r: (r['version'], 'omni' in r['database']))
    dll = root.parent/'bm2dx.dll'
    result['dll_references'] = []
    if dll.is_file():
        stat = dll.stat()
        result['dll_references'] = dll_formats(str(dll), stat.st_size, stat.st_mtime_ns)
    return result


def visual_info(original):
    root = data_root(original); info = metadata(original)
    mid = song_id(original)
    stem = info['bga'] if info and re.fullmatch(r'[A-Za-z0-9_-]+', info['bga']) else file_id(original)
    bga = next((root/'movie'/(stem + ext) for ext in ['.mp4','.wmv','.s3v','.mpg','.mpeg','.4']
                if root and (root/'movie'/(stem + ext)).is_file()), None)
    ident=file_id(original)
    layer = next((root/folder/(ident + ext) for folder in ['graphic','graph','graph_data/sys'] for ext in ['.ifs','.bin']
                  if root and ident and (root/folder/(ident + ext)).is_file()), None)
    if layer is None and root and ident:
        layer=next((root/folder/ident/name for folder in ['graph/sys','graph_data/sys'] for name in ['owl.bin','osprey.bin','system.idx'] if (root/folder/ident/name).is_file()),None)
    return dict(title=info['title'] if info else '', bga_path=str(bga) if bga else None,
                layer_path=str(layer) if layer else None,
                bga_offset=info['bga_offset'] if info else 0,
                layer_animation=info['afp'][1] if info and len(info['afp']) > 1 else '',
                evidence='曲库' + (' · DLL 引用' if info and info['dll_references'] else '') if info else '同编号候选',
                metadata=info)


@lru_cache(maxsize=8)
def catalog_cached(signature):
    songs = {}
    for path, size, stamp in signature:
        try:
            data, version, count, start, stride = database(path,size,stamp)
            id_at = id_offset(stride,version)
            for at in range(start,len(data),stride):
                mid = struct.unpack_from('<I',data,at+id_at)[0]
                key=canonical_id(mid,stride in LEGACY_IDS)
                if key in songs and songs[key]['version'] >= version: continue
                item = table_entry(path,mid,data,version,count,start,stride)
                if item: songs[key] = {k:item[k] for k in ['title','title_ascii','artist','bga','version','legacy','id_text']}
        except (ValueError,struct.error):
            pass
    return songs


def catalog(root):
    paths = [root/folder/name for folder in ['info/0','info/1','info']
             for name in ['music_omni.bin','music_data.bin','eout.bin'] if (root/folder/name).is_file()]
    return catalog_cached(tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in paths))


@lru_cache(maxsize=8)
def nearby_catalog(root):
    """Earlier games may embed their names in the executable. Reuse only title
    labels from a known sibling game's table, never its playback settings."""
    root=Path(root)
    game=next((p for p in root.parents if re.match(r'^\d{2}_',p.name)),None)
    if game is None:return {}
    for sibling in sorted(game.parent.iterdir(),reverse=True):
        if not re.match(r'^\d{2}_',sibling.name):continue
        for folder in ['data','contents/data','contents/modules/data']:
            candidate=sibling/folder
            if candidate==root:continue
            songs=catalog(candidate)
            if songs:return songs
    return {}


def annotate(entries):
    roots = {};parents={}
    for item in entries:
        parent=item.path.parent
        if parent not in parents:parents[parent]=next((p for p in item.path.parents if p.name.lower()=='data'),None)
        root=parents[parent]
        if root is None: yield item, ''; continue
        if root not in roots:
            songs = catalog(root); movies = {};fallback=None
            if any(not song.get('artist') for song in songs.values()):fallback=nearby_catalog(str(root))
            for key,song in songs.items():
                if song['bga']:
                    artist=song.get('artist') or (fallback or {}).get(key,{}).get('artist','')
                    movies.setdefault(song['bga'].lower(),[]).append(song['title']+' '+song['title_ascii']+' '+artist)
            roots[root] = songs,movies,fallback
        songs,movies,fallback = roots[root]
        ident=file_id(item.path);mid=canonical_id(int(ident),len(ident)==4) if ident else None; song = songs.get(mid)
        if mid is not None and (song is None or not song.get('artist')):
            if fallback is None:
                fallback=nearby_catalog(str(root));roots[root]=songs,movies,fallback
            if song is None:song=fallback.get(mid)
            elif mid in fallback:song={**fallback[mid],**song,'artist':fallback[mid].get('artist','')}
        title = song['title']+' '+song['title_ascii']+' '+song.get('artist','') if song else ''
        if title and ident:title+=' '+ident+f' {mid:05}'
        if item.path.parent.name.lower() == 'movie':
            title = ' · '.join(movies.get(item.path.stem.lower(),[])) or title
        yield item,title


def song_labels(original):
    root=data_root(original);key=song_key(original)
    if root is None:return dict(song_title='',song_artist='')
    songs=catalog(root);song=songs.get(key)
    if song is None and Path(original).parent.name.lower()=='movie':
        matches=[(mid,s) for mid,s in songs.items() if s['bga'].lower()==Path(original).stem.lower()]
        if len(matches)==1:key,song=matches[0]
    if key is not None and (song is None or not song.get('artist')):
        fallback=nearby_catalog(str(root)).get(key,{})
        if song is None:song=fallback
        elif fallback:song=dict(song,artist=fallback.get('artist',''))
    return dict(song_title=song.get('title','') if song else '',song_artist=song.get('artist','') if song else '')


def related_songs(original):
    root = data_root(original)
    if root is None: return []
    path = Path(original); songs = catalog(root); mid = song_key(path)
    if not songs:songs=nearby_catalog(str(root))
    ids = [mid] if path.parent.name.lower() != 'movie' and mid in songs else [
        number for number,song in songs.items() if song['bga'].lower() == path.stem.lower()]
    result = []
    for number in ids:
        stems=[f'{number:05}']
        if number%1000<100:stems.insert(0,f'{number//1000*100+number%1000:04}')
        sources=[root/'sound'/(stem+'.ifs') for stem in stems]
        sources += [root/folder/stem/(stem+suffix+ext) for stem in stems for suffix in ['', '1','2']
                    for ext in ['.s3p','.2dx'] for folder in ['sound','sd_data']]
        source=next((p for p in sources if p.is_file()),None)
        if source:
            stem=file_id(source)
            result.append(dict(id=str(number),name=f"{songs[number]['title']} · {stem}",path=str(source)))
    return result
