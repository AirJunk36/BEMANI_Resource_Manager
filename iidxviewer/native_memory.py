"""Bound Cocoa temporaries in the worker's blocking request loop."""
from contextlib import contextmanager
import sys

runtime = None
if sys.platform == 'darwin':
    import ctypes
    runtime = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    runtime.objc_autoreleasePoolPush.argtypes = []
    runtime.objc_autoreleasePoolPush.restype = ctypes.c_void_p
    runtime.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
    runtime.objc_autoreleasePoolPop.restype = None


@contextmanager
def native_scope():
    pool = runtime.objc_autoreleasePoolPush() if runtime else None
    try:
        yield
    finally:
        if runtime:
            runtime.objc_autoreleasePoolPop(pool)
