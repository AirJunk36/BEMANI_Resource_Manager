"""Read-only Layer corpus audit. Run from the project with PYTHONPATH=.."""
import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
import time

from PySide6.QtGui import QGuiApplication
from iidxviewer.actions import Program
from iidxviewer.resources import open_package
from iidxviewer.render import SceneRenderer


def candidates(roots):
    for root in roots:
        if root.is_file():yield root;continue
        for path in root.rglob('*'):
            if not {'graph','graphic','graph_data'} & set(path.parts):continue
            if path.suffix.lower() in {'.ifs','.bin'} and path.stem.isdigit():yield path
            elif path.parent.name.isdigit() and path.name.lower() in {'owl.bin','osprey.bin','system.idx'}:yield path


def digest(path):
    files=[path]
    if path.name.lower() in {'owl.bin','osprey.bin','system.idx'}:
        files=sorted(p for p in path.parent.iterdir() if p.is_file() and not p.name.startswith('.'))
    value=hashlib.sha256()
    for file in files:
        value.update(file.name.encode())
        with file.open('rb') as stream:
            while chunk:=stream.read(4*1024*1024):value.update(chunk)
    return value.hexdigest()


def inspect_scripts(package):
    modes=Counter();calls=Counter();seen=set();scripts=set()
    for animation in package.animations:
        if getattr(animation,'legacy',False):continue
        for timeline in [animation.timeline,*[data for kind,data in animation.movie.definitions.values() if kind=='sprite']]:
            if id(timeline) in seen:continue
            seen.add(id(timeline))
            for code,value in timeline.tags:
                if code==127:
                    if 'blend' in value:modes[value['blend']]+=1
                    blobs=[blob for _,blob in value.get('events',[])]
                else:blobs=[value] if code==122 else []
                for blob in blobs:
                    if blob in scripts:continue
                    scripts.add(blob)
                    program=Program(blob,getattr(animation.movie,'string',str))
                    for op,args,_ in program.actions.values():
                        if op==67:
                            for arg in args:
                                if isinstance(arg,str) and arg.startswith(('aep_','deep','goto')):calls[arg]+=1
    return dict(modes),dict(calls)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots',type=Path,nargs='+');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gpu',action='store_true');parser.add_argument('--parse-only',action='store_true')
    parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    app=QGuiApplication.instance() or QGuiApplication([])
    device=None
    if args.gpu and not args.parse_only:
        from iidxviewer.gpu import GpuDevice
        device=GpuDevice()
    rows=[];seen={};start=time.perf_counter()
    files=sorted(set(candidates(args.roots)));report=dict(files=len(files),rows=rows,device=device.info if device else dict(api='CPU'))
    completed=set();previous_seconds=0
    if args.resume and args.output.exists():
        previous=json.loads(args.output.read_text());rows.extend(r for r in previous['rows'] if not r.get('error') and not r.get('errors'));previous_seconds=previous.get('seconds',0)
        completed={r['path'] for r in rows}
        seen.update((r['sha256'],r['path']) for r in rows if 'sha256' in r and 'same_as' not in r)
        start-=previous_seconds
    try:
        for number,path in enumerate(files,1):
            if str(path.resolve()) in completed:continue
            row=dict(path=str(path.resolve()));rows.append(row);package=None
            try:
                checksum=digest(path);row['sha256']=checksum
                if checksum in seen:row['same_as']=seen[checksum];continue
                seen[checksum]=row['path'];package=open_package(path)
                row.update(animations=len(package.animations),warnings=package.warnings,issues={},frames=0)
                row['blend_modes'],row['calls']=inspect_scripts(package)
                for animation in (() if args.parse_only else package.animations):
                    if '/' in animation.name:continue
                    if device:
                        from iidxviewer.gpu import GpuSceneRenderer
                        renderer=GpuSceneRenderer(package,animation,device)
                    else:renderer=SceneRenderer(package,animation)
                    try:
                        length=animation.length if getattr(animation,'legacy',False) else len(animation.timeline.frames)
                        for frame in sorted({0,min(10,max(0,length-1)),min(60,max(0,length-1))}):
                            renderer.render(frame,(160,120));row['frames']+=1
                        if renderer.issues:row['issues'][animation.name]=sorted(renderer.issues)
                    except Exception as error:row.setdefault('errors',{})[animation.name]=repr(error)
                    finally:renderer.close()
            except Exception as error:row['error']=repr(error)
            finally:
                if device:device.reset_textures()
                if package:package.close()
            if number%10==0 or row.get('error') or row.get('errors'):
                print(number,'/',len(files),'unique',len(seen),'frames',sum(r.get('frames',0) for r in rows),'seconds',round(time.perf_counter()-start,1),flush=True)
                gc.collect();report.update(unique=len(seen),seconds=time.perf_counter()-start)
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        report.update(unique=len(seen),seconds=time.perf_counter()-start)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if device:device.close()
    print('DONE',report['files'],report['unique'],'frames',sum(r.get('frames',0) for r in rows),round(report['seconds'],1),flush=True)


if __name__=='__main__':main()
