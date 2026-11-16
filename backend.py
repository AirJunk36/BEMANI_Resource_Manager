"""JSON-lines worker for the SwiftUI and WinUI 3 applications. No visible Qt UI."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "darwin":
    os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")

from PySide6.QtGui import QGuiApplication
from iidxviewer.service import ResourceService


def main():
    if os.name == 'posix' and os.environ.get('IIDX_BACKGROUND_WORKER') == '1' and os.getpgrp() != os.getpid():
        os.setsid()
    # The native clients use UTF-8 regardless of the Windows console code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache")
    args = parser.parse_args()
    app = QGuiApplication(sys.argv[:1])
    owned = tempfile.TemporaryDirectory(prefix="iidx-native-") if not args.cache else None
    cache = Path(args.cache or owned.name)
    cache.mkdir(parents=True, exist_ok=True)
    service = ResourceService(cache)
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                with contextlib.redirect_stdout(sys.stderr):
                    result = service.dispatch(request)
                response = {"id": request.get("id"), "ok": True, "result": result}
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                response = {"id": request.get("id"), "ok": False, "error": str(exc)}
            try:
                encoded=json.dumps(response,ensure_ascii=False,separators=(",",":"),allow_nan=False)
            except (TypeError,ValueError):
                traceback.print_exc(file=sys.stderr)
                encoded=json.dumps(dict(id=request.get('id'),ok=False,error='解析结果格式错误'),ensure_ascii=False)
            print(encoded,flush=True)
    finally:
        service.shutdown()
        if owned:
            owned.cleanup()


if __name__ == "__main__":
    main()
