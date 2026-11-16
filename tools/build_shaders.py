"""Bake portable GPU shaders once; qsb is a build dependency only."""
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
compiler = shutil.which("qsb")
if not compiler:
    raise SystemExit("Install Qt Shader Tools (qsb) to rebuild shaders.")
for path in sorted((root / "iidxviewer/shaders").glob("scene.*")):
    if path.suffix not in {".vert", ".frag"}:
        continue
    subprocess.run([compiler, "--glsl", "150,300 es", "--hlsl", "50", "--msl", "12", "-o", str(path) + ".qsb", str(path)], check=True)
    print(path.name + ".qsb")
