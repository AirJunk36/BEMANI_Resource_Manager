"""Compare identical scene logic: CPU + PNG versus GPU + shared BGRA frames."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
HOME=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HOME))
from PIL import Image
from PySide6.QtGui import QGuiApplication,QImage
from iidxviewer.afp import Animation,Movie,Timeline
from iidxviewer.frames import SharedFrames
from iidxviewer.gpu import GpuDevice,GpuSceneRenderer
from iidxviewer.render import SceneRenderer
from iidxviewer.resources import Package,open_package


def synthetic():
    tags=[];frames=[]
    for frame in range(24):
        start=len(tags)
        for i in range(96):
            tags.append((127,{'flags':2 if frame==0 else 1,'character':1,'depth':i+1,
                             'position':((i*53+frame*3)%600,(i*41+frame*2)%440),
                             'multiply':(.3+(i+frame)%60/100,.4+(i*3+frame)%50/100,.5, .7)}))
        frames.append((start,len(tags)-start))
    timeline=Timeline(frames,tags)
    movie=Movie('dense',timeline,(0,640,0,480),30,{1:('image','sprite')},[])
    p=Package(Path('synthetic.ifs'));image=Image.new('RGBA',(32,32),(255,150,90,220));p.texture=lambda name:image
    return p,Animation('96 tinted sprites',movie,timeline)


def summary(values):
    ordered=sorted(values)
    return {'median_ms':round(statistics.median(values),3),'p95_ms':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],3)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(HOME.parent));parser.add_argument('--out',default=str(HOME/'build/performance/comparison.json'))
    args=parser.parse_args();app=QGuiApplication([]);device=GpuDevice();report={'device':device.info,'method':'same scene logic, warm resources, includes frame transfer and pixel decode','cases':[]}
    cases=[('20_tricoro/data/graphic/test_effect_anime.ifs',0),('18_Resort_Anthem/data/graph_data/sys/0200.bin',0),('33_Sparkle_Shower/data/graphic/0/mselect.ifs',None),(None,0)]
    try:
        with tempfile.TemporaryDirectory(prefix='iidx-bench-') as folder:
            ring=SharedFrames(folder)
            try:
                for relative,index in cases:
                    path=Path(args.root)/relative if relative else None
                    if path and not path.exists():continue
                    before=hashlib.sha256(path.read_bytes()).hexdigest() if path else None
                    if path:
                        p=open_package(path)
                        if index is None:
                            from iidxviewer.scene import Scene
                            ordered=sorted(range(len(p.animations)),key=lambda i:(p.animations[i].movie.rect[1]-p.animations[i].movie.rect[0])*(p.animations[i].movie.rect[3]-p.animations[i].movie.rect[2]),reverse=True)
                            index=next((i for i in ordered if Scene(p,p.animations[i]).draws(min(3,p.animations[i].length-1))),ordered[0])
                        a=p.animations[index]
                    else:p,a=synthetic()
                    try:
                        x0,x1,y0,y1=a.movie.rect;scale=min(1,1200/max(x1-x0,y1-y0));size=max(1,round((x1-x0)*scale)),max(1,round((y1-y0)*scale))
                        cpu=SceneRenderer(p,a);gpu=GpuSceneRenderer(p,a,device)
                        cpu.render(0,size);gpu.render(0,size)
                        samples={'cpu_render':[],'cpu_png_roundtrip':[],'gpu_render':[],'gpu_shared_roundtrip':[]}
                        frames=min(24,a.length)
                        for backend in ('cpu','gpu'):
                            renderer=cpu if backend=='cpu' else gpu
                            for frame in range(frames):
                                begin=time.perf_counter();image=renderer.render(frame,size);rendered=time.perf_counter()
                                if backend=='cpu':
                                    png=Path(folder)/'frame.png';image.save(str(png),'PNG');decoded=QImage(str(png));decoded.pixelColor(0,0)
                                else:
                                    reply=ring.write(image);pixels=ring.memory[reply['offset']:reply['offset']+reply['bytes']]
                                    decoded=QImage(pixels,*size,size[0]*4,QImage.Format.Format_ARGB32_Premultiplied).copy();decoded.pixelColor(0,0)
                                end=time.perf_counter()
                                samples[backend+'_render'].append((rendered-begin)*1000)
                                samples[backend+('_png_roundtrip' if backend=='cpu' else '_shared_roundtrip')].append((end-begin)*1000)
                        result={'source':relative or 'synthetic: 96 independently tinted sprites','animation':a.name,'size':size,'frames':frames,
                                'draw_calls_at_frame_3':len(gpu.scene.draws(min(3,a.length-1))),
                                **{key:summary(value) for key,value in samples.items()}}
                        result['speedup']=round(result['cpu_png_roundtrip']['median_ms']/max(.001,result['gpu_shared_roundtrip']['median_ms']),2)
                        result['source_unchanged']=not path or hashlib.sha256(path.read_bytes()).hexdigest()==before
                        report['cases'].append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
                    finally:p.close();device.reset_textures()
            finally:ring.close()
    finally:device.close()
    target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
