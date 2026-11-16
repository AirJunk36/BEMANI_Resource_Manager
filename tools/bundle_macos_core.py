"""Build a relocatable, signed Python runtime from the installed build dependencies."""
from collections import deque
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

MAGIC = {bytes.fromhex(v) for v in ('cffaedfe', 'cefaedfe', 'feedfacf', 'feedface', 'cafebabe', 'bebafeca')}
PACKAGES = ('ifstools', 'kbinxml', 'tqdm', 'PIL', 'lxml', 'numpy', 'Crypto', 'imageio_ffmpeg', 'shiboken6')


def ignored(directory, names):
    return [n for n in names if n in {'__pycache__', '_CodeSignature', 'tests', 'test', 'include', 'Headers', 'direct_url.json', 'ensurepip', 'idlelib', 'tkinter', 'Python.app', 'site-packages', 'pkgconfig'}
            or n.startswith(('.ghost-', '_test', '_ctypes_test', 'xxlimited', 'xxsubtype', '_xxtest', 'config-')) or n.endswith(('.pyc', '.pyo', '.pyi', '.a', '.h'))]


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in MAGIC


def output(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def dependencies(path):
    ids = output(['otool', '-D', str(path)]).splitlines()[1:]
    return [line.strip().split(' (', 1)[0] for line in output(['otool', '-L', str(path)]).splitlines()[1:]
            if ' (compatibility version ' in line and line.strip().split(' (', 1)[0] not in ids]


def sign(path):
    subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def framework_links(framework, version):
    versions = framework / 'Versions'
    current = versions / 'Current'
    if not current.exists():
        current.symlink_to(version)
    for name in (framework.stem, 'Resources'):
        target = framework / name
        if target.is_symlink():
            continue
        versioned = versions / version / name
        if target.is_dir():
            shutil.copytree(target, versioned, dirs_exist_ok=True)
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        target.symlink_to(Path('Versions/Current') / name)


def prepare_runtime(home):
    framework = Path(sys.base_prefix).resolve().parents[1]
    packages=PACKAGES + (('live2d',) if os.environ.get('IIDX_INCLUDE_LIVE2D') == '1' else ())
    modules = {name: Path(importlib.import_module(name).__file__).resolve().parent for name in packages}
    modules['PySide6'] = Path(importlib.import_module('PySide6').__file__).resolve().parent
    spec = dict(format=3, python=sys.version, arch=platform.machine(), framework=str(framework),
                packages={name: [str(path), (path / '__init__.py').stat().st_mtime_ns] for name, path in modules.items()})
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    cache = Path(tempfile.gettempdir()) / ('bemani-core-runtime-' + key[:16])
    manifest = cache / 'runtime.json'
    if manifest.exists() and json.loads(manifest.read_text()).get('key') == key:
        return cache
    print('Preparing embedded Python and decoding dependencies…', flush=True)
    with tempfile.TemporaryDirectory(prefix='bemani-core-build-') as directory:
        root = Path(directory) / 'Core'; root.mkdir()
        python = root / 'Python.framework'
        shutil.copytree(framework, python, symlinks=True, ignore=ignored)
        sources = [(framework, python)]
        for name, path in modules.items():
            destination = root / 'site-packages' / name
            if name != 'PySide6':
                shutil.copytree(path, destination, symlinks=True, ignore=ignored)
            else:
                destination.mkdir(parents=True)
                for item in path.iterdir():
                    if item.is_file() and (item.suffix == '.py' or item.name.startswith('libpyside6.abi3.') or item.name in {'QtCore.abi3.so', 'QtGui.abi3.so'}):
                        shutil.copy2(item, destination / item.name)
                shutil.copytree(path / 'support', destination / 'support', ignore=ignored)
                for name_ in ('QtCore', 'QtGui', 'QtDBus'):
                    target = destination / 'Qt/lib' / (name_ + '.framework')
                    shutil.copytree(path / 'Qt/lib' / target.name, target, symlinks=True, ignore=ignored)
                    framework_links(target, 'A')
                plugins = destination / 'Qt/plugins/platforms'; plugins.mkdir(parents=True)
                shutil.copy2(path / 'Qt/plugins/platforms/libqoffscreen.dylib', plugins)
            sources.append((path, destination))
        # Keep distribution metadata and licences without development tools.
        for name in ('ifstools', 'kbinxml', 'tqdm', 'pillow', 'lxml', 'numpy', 'pycryptodome', 'imageio-ffmpeg', 'shiboken6', 'PySide6-Essentials') + (('live2d-py',) if 'live2d' in modules else ()):
            dist = importlib.metadata.distribution(name)
            metadata = Path(dist._path)
            shutil.copytree(metadata, root / 'site-packages' / metadata.name, ignore=ignored)
        framework_links(python, f'{sys.version_info.major}.{sys.version_info.minor}')
        (root / 'bin').mkdir()
        # Homebrew's bin/python is a launcher that spawns Python.app. Embed the
        # actual interpreter, so no launcher or installed framework is needed.
        interpreter = framework / f'Versions/{sys.version_info.major}.{sys.version_info.minor}/Resources/Python.app/Contents/MacOS/Python'
        shutil.copy2(interpreter, root / 'bin/python3')
        native = deque(p for p in root.rglob('*') if macho(p))
        seen = set()
        external = {}
        while native:
            path = native.popleft()
            if path in seen:
                continue
            seen.add(path)
            archs = output(['lipo', '-archs', str(path)]).split()
            if platform.machine() not in archs:
                raise RuntimeError(f'Wrong runtime architecture: {path}')
            if len(archs) > 1:
                thin = path.with_name(path.name + '.thin')
                subprocess.run(['lipo', str(path), '-thin', platform.machine(), '-output', str(thin)], check=True)
                thin.replace(path)
            changes = []
            for dep in dependencies(path):
                if not dep.startswith('/') or dep.startswith(('/usr/lib/', '/System/Library/')):
                    continue
                real = Path(dep).resolve()
                target = next((dst / real.relative_to(src) for src, dst in sources if real.is_relative_to(src) and (dst / real.relative_to(src)).exists()), None)
                if target is None:
                    if real not in external:
                        target = root / 'lib' / real.name
                        if target.exists():
                            target = target.with_name(hashlib.sha256(str(real).encode()).hexdigest()[:8] + '-' + real.name)
                        target.parent.mkdir(exist_ok=True)
                        shutil.copy2(real, target); external[real] = target; native.append(target)
                    target = external[real]
                changes += ['-change', dep, '@loader_path/' + os.path.relpath(target, path.parent)]
            # Remove build-machine search paths; relative Qt/wheel paths remain.
            load = output(['otool', '-l', str(path)])
            for entry in re.findall(r'cmd LC_RPATH\n.*?path (.*?) \(offset', load, re.S):
                if entry.startswith('/') and not entry.startswith(('/System/', '/usr/lib/')):
                    changes += ['-delete_rpath', entry]
            ids = output(['otool', '-D', str(path)]).splitlines()[1:]
            if ids and ids[0].startswith('/'):
                changes += ['-id', '@rpath/' + path.relative_to(root).as_posix()]
            if changes:
                subprocess.run(['install_name_tool', *changes, str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for path in seen:
            for dep in dependencies(path):
                if dep.startswith('/') and not dep.startswith(('/usr/lib/', '/System/Library/')):
                    raise RuntimeError(f'Nonportable dependency: {path}: {dep}')
            sign(path)
        for path in sorted(root.rglob('*.framework'), key=lambda p: len(p.parts), reverse=True):
            sign(path)
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(path)], check=True)
        (root / 'runtime.json').write_text(json.dumps(dict(key=key, python=f'{sys.version_info.major}.{sys.version_info.minor}', native_files=len(seen), spec=spec), indent=2))
        if cache.exists():
            shutil.rmtree(cache)
        root.rename(cache)
    return cache


def embed_core(home, resources):
    runtime = prepare_runtime(home)
    target = resources / 'Core'
    def runtime_ignored(directory, names):
        return ['doc'] if Path(directory).relative_to(runtime).match('Python.framework/Versions/*/share') else []
    shutil.copytree(runtime, target, symlinks=True, ignore=runtime_ignored)
    # Framework examples are build tools, not runtime dependencies. Keep their
    # licence while omitting documentation and development repository files.
    framework = target / 'Python.framework'
    for docs in (runtime / 'Python.framework').glob('Versions/*/share/doc'):
        if docs.is_symlink() or not docs.exists():
            continue
        for license in docs.rglob('LICENSE*'):
            if license.is_file():
                destination = target / 'licenses/python-docs' / license.relative_to(docs)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(license, destination)
    sign(framework)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(framework)], check=True)
    manifest = json.loads((runtime / 'runtime.json').read_text())
    (target / 'runtime.json').write_text(json.dumps(dict(python=manifest['python'], native_files=manifest['native_files'], architecture=platform.machine()), indent=2))
    shutil.copy2(home / 'backend.py', target)
    shutil.copy2(home / 'requirements.txt', target)
    shutil.copytree(home / 'iidxviewer', target / 'iidxviewer', ignore=lambda directory,names: ignored(directory,names) + ([] if os.environ.get('IIDX_INCLUDE_LIVE2D') == '1' else ['Live2DCubismCore.dll']))
    env = dict(os.environ, PYTHONHOME=str(target / 'Python.framework/Versions/Current'),
               PYTHONPATH=str(target / 'site-packages') + ':' + str(target), PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', QT_QPA_PLATFORM='offscreen', QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM='1',
               QT_PLUGIN_PATH=str(target / 'site-packages/PySide6/Qt/plugins'), PYTHONSAFEPATH='1', PATH='/usr/bin:/bin:/usr/sbin:/sbin')
    probe = 'from PySide6.QtGui import QGuiApplication; from iidxviewer.service import ResourceService; from iidxviewer.gpu import GpuDevice; import Crypto.Cipher.AES, numpy, imageio_ffmpeg; app=QGuiApplication([]); device=GpuDevice(); print(device.info); device.close()'
    subprocess.run([str(target / 'bin/python3'), '-c', probe], env=env, cwd=target, check=True)
    print('Embedded decoding core ready', flush=True)
