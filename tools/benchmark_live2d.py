"""Measure uncached Live2D preview frames, separating texture preparation."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QGuiApplication
from iidxviewer.service import ResourceService

parser=argparse.ArgumentParser()
parser.add_argument('model',type=Path)
parser.add_argument('--frames',type=int,default=180)
parser.add_argument('--resolution',default='1080p')
parser.add_argument('--renderer',choices=['gpu','cpu'],default='gpu')
parser.add_argument('--output',type=Path)
args=parser.parse_args()
app=QGuiApplication([])
with tempfile.TemporaryDirectory(prefix='bemani-live2d-bench-') as folder:
    service=ResourceService(Path(folder))
    try:
        started=time.perf_counter();service.open(args.model);opened=time.perf_counter()-started
        index=next((i for i,a in enumerate(service.package.animations) if a.group=='Idle'),0)
        length=service.package.animations[index].length
        start=time.perf_counter();first=service.render(index,0,transport='shared',resolution=args.resolution,renderer=args.renderer);prepared=time.perf_counter()-start
        samples=[]
        for i in range(1,min(max(2,args.frames+1),length)):
            start=time.perf_counter();service.render(index,i,transport='shared',resolution=args.resolution,renderer=args.renderer);samples.append((time.perf_counter()-start)*1000)
        ordered=sorted(samples)
        report=dict(model=args.model.name,resolution=args.resolution,renderer=first['renderer'],frames=len(samples),open_ms=opened*1000,first_frame_ms=prepared*1000,
                    median_ms=statistics.median(samples) if samples else None,p95_ms=ordered[min(len(ordered)-1,int(len(ordered)*.95))] if ordered else None)
        if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:service.close()
