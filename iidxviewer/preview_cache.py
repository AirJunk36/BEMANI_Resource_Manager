"""Bounded previews shared by disposable media workers; originals are never cached by link."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

ROOT=Path(tempfile.gettempdir())/'bemani-song-previews-v4'


@lru_cache(maxsize=1)
def revision():
    names=sorted(p.name for p in Path(__file__).parent.glob('*.py'))
    return hashlib.sha256(b''.join(Path(__file__).with_name(n).read_bytes() for n in names)).hexdigest()


def key(kind,files,values):
    stamps=[]
    for file in sorted(set(map(Path,files))):
        stat=file.stat();stamps.append((str(file.resolve()),stat.st_size,stat.st_mtime_ns))
    data=json.dumps([revision(),kind,stamps,values],sort_keys=True,ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def link_copy(source,target):
    try:os.link(source,target)
    except OSError:shutil.copyfile(source,target)


def get(identifier,target):
    root=ROOT;meta=root/(identifier+'.json');media=root/(identifier+Path(target).suffix)
    copying=False
    try:
        value=json.loads(meta.read_text());stat=media.stat()
        if stat.st_size!=value.pop('_bytes'):return None
        copying=True;link_copy(media,target);os.utime(meta,None)
        return dict(value,path=str(target))
    except (OSError,ValueError,KeyError):
        if copying:Path(target).unlink(missing_ok=True)
        return None


def put(identifier,reply):
    root=ROOT;root.mkdir(parents=True,exist_ok=True)
    source=Path(reply['path']);media=root/(identifier+source.suffix);meta=root/(identifier+'.json')
    temporary=root/(identifier+'.'+uuid.uuid4().hex)
    try:
        link_copy(source,temporary);os.replace(temporary,media)
        value={k:v for k,v in reply.items() if k!='path'};value['_bytes']=media.stat().st_size
        temporary.write_text(json.dumps(value));os.replace(temporary,meta)
    except OSError:temporary.unlink(missing_ok=True)
    # At most 2 GiB / 12 hours. Active players retain their own hard link.
    entries=[]
    for path in root.glob('*.json'):
        try:
            parts=list(root.glob(path.stem+'.*'));size=sum(p.stat().st_size for p in parts);entries.append((path.stat().st_mtime,size,parts))
        except OSError:continue
    total=sum(size for _,size,_ in entries)
    for stamp,size,parts in sorted(entries):
        if total<=2*1024**3 and time.time()-stamp<12*3600:break
        for path in parts:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        total-=size
