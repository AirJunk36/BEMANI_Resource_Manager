"""Run AFP metadata in an isolated Windows process, always from copied libraries."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .afp import restore
from .engines import PE


def run_worker(command, *, timeout, creationflags=0, input_data=None):
    # The backend's stdin is the native client's JSON-lines channel. prlctl
    # changes stdin's file flags and may consume input. Give helpers their own
    # payload pipe (or null device), never the native client's protocol pipe.
    stdin = dict(stdin=subprocess.DEVNULL) if input_data is None else dict(input=input_data)
    return subprocess.run(command, **stdin, capture_output=True,
                          timeout=timeout, creationflags=creationflags)


def windows_share(path):
    path = Path(path).resolve()
    parts = path.parts
    if len(parts) > 3 and parts[1] == 'Volumes':
        return '\\\\Mac\\'+'\\'.join(parts[2:])
    try:
        return '\\\\Mac\\Home\\'+str(path.relative_to(Path.home())).replace('/', '\\')
    except ValueError:
        raise ValueError('请将资源放入 Windows 共享目录') from None


def prepare_job(package, library):
    library = Path(library)
    pe = PE(library.read_bytes())
    if pe.arch != 'x64':
        raise ValueError('运行库为 '+pe.arch+'；DLL 读取仅支持 x64，将使用格式解析')
    export = next((name for name in pe.exports if re.fullmatch(r'.+[0-9a-fA-F]{6}', name)
                   and name.endswith('000044')), None)
    if export is None:
        raise ValueError('此 DLL 暂不支持动画读取')
    libraries, seen = [], set()
    available = {p.name.lower(): p for p in library.parent.iterdir() if p.is_file() and p.suffix.lower() == '.dll'}
    def add(path):
        key = path.name.lower()
        if key in seen:
            return
        seen.add(key)
        data = path.read_bytes()
        if len(data) > 64*1024*1024:
            raise ValueError('运行库过大')
        libraries.append(dict(path=str(path), sha256=hashlib.sha256(data).hexdigest()))
        for name in PE(data).imports:
            dependency = available.get(name.lower())
            if dependency:
                add(dependency)
    add(library)
    animations = []
    for name, asset in package.files.items():
        if name.startswith('afp/') and name.count('/') == 1 and not name.endswith('.xml'):
            bsi = package.files.get('afp/bsi/'+name[4:])
            data = restore(asset.read(), bsi.read() if bsi else b'')
            animations.append(dict(name=name, data=base64.b64encode(data).decode('ascii')))
    if not animations:
        raise ValueError('此资源没有 AFP 动画')
    return dict(library_name=library.name, export=export, libraries=libraries, animations=animations)


def inspect_package(package, library):
    job = prepare_job(package, library)
    worker = Path(__file__).with_name('dll_worker.py')
    if os.name == 'nt':
        with tempfile.TemporaryDirectory(prefix='bemani-inspect-', ignore_cleanup_errors=True) as directory:
            request = Path(directory)/'request.json'
            request.write_text(json.dumps(job), encoding='utf-8')
            result = run_worker([sys.executable, str(worker), str(request)],
                                timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            # Windows may briefly retain mapped DLL files after process exit.
            # A cleanup race must not discard a successful metadata response.
            copies = Path(directory)/'libraries'
            for attempt in range(8):
                try:
                    shutil.rmtree(copies)
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    time.sleep(.05)
    else:
        prlctl = Path('/Applications/Parallels Desktop.app/Contents/MacOS/prlctl')
        if not prlctl.exists():
            raise ValueError('读取 DLL 需登录 Parallels Windows')
        # Stream an owned request pipe to the guest. No host-share path or
        # write access inside the signed app is needed for the helper/job.
        for item in job['libraries']:
            item['path'] = windows_share(item['path'])
        payload = json.dumps(dict(worker=base64.b64encode(worker.read_bytes()).decode(),
                                  request=base64.b64encode(json.dumps(job).encode()).decode())).encode()
        script = r"""$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$env:PYTHONIOENCODING='utf-8'
$root=Join-Path $env:LOCALAPPDATA 'BEMANI素材管理器\runtime'
$python=$null
if(Test-Path $root){
  $python=Get-ChildItem $root -Directory | Sort-Object LastWriteTime -Descending |
    ForEach-Object {Join-Path $_.FullName '.venv\Scripts\python.exe'} |
    Where-Object {Test-Path $_} | Select-Object -First 1
}
if(!$python){throw '请先在 Windows 中启动一次本软件'}
$payload=[Console]::In.ReadToEnd() | ConvertFrom-Json
$copyDir=Join-Path ([IO.Path]::GetTempPath()) ('bemani-dll-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $copyDir | Out-Null
try {
  $worker=Join-Path $copyDir 'dll_worker.py'
  $request=Join-Path $copyDir 'request.json'
  [IO.File]::WriteAllBytes($worker,[Convert]::FromBase64String($payload.worker))
  [IO.File]::WriteAllBytes($request,[Convert]::FromBase64String($payload.request))
  & $python $worker $request (Join-Path $copyDir 'libraries')
  $workerExit=$LASTEXITCODE
} finally {
  if(Test-Path -LiteralPath $copyDir){Remove-Item -LiteralPath $copyDir -Recurse -Force}
}
if($workerExit){exit $workerExit}
"""
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        command = [str(prlctl), 'exec', os.environ.get('IIDX_WINDOWS_VM', 'Windows 11'), '--current-user',
                   'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]
        result = run_worker(command, timeout=60, input_data=payload)
    text = result.stdout.decode('utf-8-sig', 'replace')
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith('{')]
    if not lines:
        raise ValueError('DLL 读取未完成：'+result.stderr.decode('utf-8', 'replace')[-700:])
    reply = json.loads(lines[-1])
    if not reply.get('ok'):
        raise ValueError(reply.get('error', 'DLL 无法识别此动画'))
    reply['transport'] = 'local' if os.name == 'nt' else 'Parallels'
    return reply
