"""Measure real first-use frame stalls, including lazy texture decode and transfer."""
import argparse
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtGui import QGuiApplication
from iidxviewer.service import ResourceService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT.parent)
    parser.add_argument("--out", type=Path, default=ROOT / "build/responsiveness/optimized-core.json")
    parser.add_argument("--frames", type=int, default=120)
    args = parser.parse_args()
    app = QGuiApplication([])
    results = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for relative in ["33_Sparkle_Shower/data/graphic/1/title.ifs", "33_Sparkle_Shower/data/graphic/0/mselect.ifs"]:
        source = args.root / relative
        if not source.exists():
            continue
        with tempfile.TemporaryDirectory(prefix="iidx-perf-") as folder:
            service = ResourceService(folder)
            try:
                begin = time.perf_counter()
                info = service.open(source)
                opened = (time.perf_counter() - begin) * 1000
                animation = next(a for a in info["animations"] if "/" not in a["name"] and a["length"] > 60)
                begin = time.perf_counter()
                service.render(animation["id"], 0, transport="shared", resolution="1080p")
                first = (time.perf_counter() - begin) * 1000
                samples = []
                profile = cProfile.Profile()
                profile.enable()
                for frame in range(1, min(args.frames + 1, animation["length"])):
                    begin = time.perf_counter()
                    service.render(animation["id"], frame, transport="shared", resolution="1080p")
                    samples.append((time.perf_counter() - begin) * 1000)
                profile.disable()
                output = io.StringIO()
                pstats.Stats(profile, stream=output).sort_stats("cumtime").print_stats(25)
                args.out.with_name(args.out.stem + "-" + source.stem + ".profile.txt").write_text(output.getvalue())
                ordered = sorted(samples)
                row = {"source": relative, "animation": animation["name"], "open_ms": opened,
                       "first_ms": first, "median_ms": statistics.median(samples),
                       "p95_ms": ordered[int(len(ordered) * .95)], "max_ms": max(samples), "frames": len(samples)}
                results.append(row)
                print(json.dumps(row), flush=True)
            finally:
                service.close()
    args.out.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
