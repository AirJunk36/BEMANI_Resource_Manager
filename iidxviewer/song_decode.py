"""Batch compressed keysounds and reuse decoded PCM across charts and workers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT=Path(tempfile.gettempdir())/'bemani-pcm-v1'


def decode_samples(sounds,positions,folder):
    from .song import RATE,MAX_SECONDS,pcm_sample,decode_sample
    import imageio_ffmpeg
    root=ROOT;root.mkdir(parents=True,exist_ok=True)
    decoded={};pending=[]
    for sample in positions:
        blob=sounds[sample-1].read();result=pcm_sample(blob,folder,sample)
        if result is not None:decoded[sample]=result;continue
        cached=root/(hashlib.sha256(blob).hexdigest()+'.f32')
        try:
            size=cached.stat().st_size
            if size and size%8==0 and size<=RATE*MAX_SECONDS*8:
                # Give each job its own link so pruning cannot remove live input.
                target=folder/f'{sample}.f32'
                try:os.link(cached,target)
                except OSError:shutil.copyfile(cached,target)
                os.utime(cached,None);decoded[sample]=(target,size//8);continue
        except OSError:pass
        pending.append((sample,blob,cached))

    def batch(items):
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-filter_threads','1'];sources=[]
        for sample,blob,_ in items:
            path=folder/f'{sample}.audio';path.write_bytes(blob);sources.append(path)
            args+=['-threads','1','-i',str(path)]
        for i,(sample,_,_) in enumerate(items):
            args+=['-map',f'{i}:a:0','-vn','-t',str(MAX_SECONDS),'-ar',str(RATE),'-ac','2','-f','f32le',str(folder/f'{sample}.f32')]
        try:
            result=subprocess.run(args,capture_output=True,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            values=[]
            for sample,blob,cached in items:
                path=folder/f'{sample}.f32'
                if result.returncode or not path.is_file() or path.stat().st_size%8:
                    path.unlink(missing_ok=True);path,count=decode_sample(blob,folder,sample)
                else:count=path.stat().st_size//8
                if count:
                    temporary=root/(uuid.uuid4().hex+'.tmp')
                    try:
                        try:os.link(path,temporary)
                        except OSError:shutil.copyfile(path,temporary)
                        os.replace(temporary,cached)
                    except OSError:temporary.unlink(missing_ok=True)
                values.append((sample,(path,count)))
            return values
        finally:
            for path in sources:path.unlink(missing_ok=True)

    groups=[pending[i:i+12] for i in range(0,len(pending),12)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for values in workers.map(batch,groups):decoded.update(values)
    # Bounded generated data; live jobs retain their own links.
    files=[]
    for path in root.glob('*.f32'):
        try:stat=path.stat();files.append((stat.st_mtime,stat.st_size,path))
        except OSError:pass
    total=sum(size for _,size,_ in files)
    for stamp,size,path in sorted(files):
        if total<=1024**3 and time.time()-stamp<12*3600:break
        try:path.unlink(missing_ok=True)
        except OSError:pass
        total-=size
    return decoded
