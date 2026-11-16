"""Windows-only, disposable process for AFP metadata. No AVS boot or game startup."""
import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys


def inspect(job, directory):
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise ValueError('DLL 元数据读取器需要 64 位 Windows 进程')
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for entry in job['libraries']:
        path = Path(entry['path'])
        data = path.read_bytes()
        if len(data) > 64*1024*1024 or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('DLL 副本校验失败：'+path.name)
        (target/path.name).write_bytes(data)
    with os.add_dll_directory(str(target)):
        # A worker owns the module until process exit; never unload a live DLL.
        module = ctypes.CDLL(str(target/job['library_name']))
        function = getattr(module, job['export'])
        # Verified against the actual IIDX33 2.14.28 x64 metadata entry point:
        # output buffer, restored AFP bytes, byte length (not a stream index).
        function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        function.restype = ctypes.c_int
        results = []
        for item in job['animations']:
            data = base64.b64decode(item['data'], validate=True)
            if not 56 <= len(data) <= 64*1024*1024:
                raise ValueError('AFP 大小无效')
            source = ctypes.create_string_buffer(data)
            output = ctypes.create_string_buffer(4096)
            code = function(output, source, len(data))
            width, height, fps, frames = struct.unpack_from('<HHfI', output.raw, 16)
            valid = code == 0 and width > 0 and height > 0 and math.isfinite(fps) and 0 < fps <= 1000 and 0 < frames <= 1000000
            results.append(dict(name=item['name'], ok=valid, code=code, width=width,
                                height=height, fps=fps, frames=frames))
        return dict(ok=all(item['ok'] for item in results), animations=results,
                    library_sha256=job['libraries'][0]['sha256'])


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')), sys.argv[2] if len(sys.argv)>2 else Path(sys.argv[1]).parent/'libraries')), flush=True)
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error))), flush=True)
        sys.exit(1)
