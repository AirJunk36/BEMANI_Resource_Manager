"""Measure resource retention while repeatedly browsing real animations."""
import argparse
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import weakref

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--module-root', type=Path, default=root)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.module_root))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
from iidxviewer.service import ResourceService

app = QGuiApplication([])
gc.collect(); gc.disable()
references, samples = [], []
with tempfile.TemporaryDirectory(prefix='bemani-browse-') as folder:
    service = ResourceService(folder)
    for _ in range(6):
        begin = time.perf_counter()
        info = service.dispatch(dict(method='open', path=str(root.parent/'33_Sparkle_Shower/data/graphic/0/title.ifs')))
        service.dispatch(dict(method='render', animation=info['animations'][0]['id'], frame=264, renderer='cpu', resolution='480p', transport='shared'))
        references.append(weakref.ref(service.package))
        samples.append(dict(ms=round((time.perf_counter()-begin)*1000,1),
                            rss_mb=int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())]))/1024))
    service.close()
    report = dict(cycles=len(samples), packages_retained_without_gc=sum(p() is not None for p in references), samples=samples)
gc.enable(); gc.collect()
args.out.parent.mkdir(parents=True,exist_ok=True)
args.out.write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
