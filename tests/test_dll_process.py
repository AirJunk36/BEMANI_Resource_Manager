"""An inspector must never read or alter the native client's protocol pipe."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class DllProcessTests(unittest.TestCase):
    def check_protocol_pipe(self, exit_code):
        # Send a real next request through the parent process's stdin. A child
        # inheriting that pipe would consume it before the parent can handle it.
        worker = "import os,sys; print(os.read(0,4096).hex()); sys.exit("+str(exit_code)+")"
        parent = """
import json,sys
from iidxviewer.dll_inspection import run_worker
result=run_worker([sys.executable,'-c',sys.argv[1]],timeout=10)
print(json.dumps(dict(child_input=result.stdout.decode().strip(),exit_code=result.returncode,
                      next_request=sys.stdin.readline().strip())))
"""
        result = subprocess.run([sys.executable, '-c', parent, worker], input='next-native-request\n',
                                text=True, encoding='utf-8', capture_output=True,
                                cwd=Path(__file__).resolve().parents[1], timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply=json.loads(result.stdout)
        self.assertEqual(reply['child_input'], '')
        self.assertEqual(reply['next_request'], 'next-native-request')
        self.assertEqual(reply['exit_code'], exit_code)

    def test_successful_inspection_keeps_next_native_request(self):
        self.check_protocol_pipe(0)

    def test_failed_inspection_keeps_next_native_request(self):
        self.check_protocol_pipe(17)
