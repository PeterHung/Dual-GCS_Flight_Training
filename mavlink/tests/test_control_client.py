#!/usr/bin/env python3
"""Verify the real root CLI can receive replies from an unprivileged relay."""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ASSETS = Path(__file__).resolve().parents[1] / "assets"


@unittest.skipUnless(os.geteuid() == 0, "Run as root to test real cross-account socket permissions")
class ControlClientTests(unittest.TestCase):
    def test_root_controller_and_unprivileged_relay(self):
        loader = importlib.machinery.SourceFileLoader("control_client", str(ASSETS / "gcs-student-control"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        client = importlib.util.module_from_spec(spec)
        loader.exec_module(client)
        nobody = pwd.getpwnam("nobody")
        with tempfile.TemporaryDirectory(prefix="gcs-control-") as directory:
            base = Path(directory)
            base.chmod(0o755)
            runtime = base / "runtime"
            runtime.mkdir(mode=0o750)
            os.chown(runtime, nobody.pw_uid, nobody.pw_gid)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as student:
                student.bind(("127.0.0.1", 0))
                code = """
import importlib.util, pathlib, signal, sys
spec = importlib.util.spec_from_file_location("relay", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cfg = {"hotspot_ip":"127.0.0.1", "student_ip":"127.0.0.1", "boot_default":"control"}
relay = module.StudentRelay(cfg, pathlib.Path(sys.argv[2]), telemetry_port=0,
                            student_port=0, qgc_port=int(sys.argv[3]))
signal.signal(signal.SIGTERM, lambda *_: setattr(relay, "running", False))
relay.serve()
"""
                server = subprocess.Popen(
                    [sys.executable, "-B", "-c", code, str(ASSETS / "student-relay.py"),
                     str(runtime), str(student.getsockname()[1])],
                    user=nobody.pw_uid, group=nobody.pw_gid, extra_groups=[])
                try:
                    deadline = time.monotonic() + 3
                    while not (runtime / "control.sock").exists():
                        if server.poll() is not None or time.monotonic() > deadline:
                            self.fail("Unprivileged relay did not start")
                        time.sleep(0.02)
                    self.assertEqual(client.request("status", runtime)["mode"], "control")
                    self.assertEqual(client.request("off", runtime)["mode"], "monitor")
                    self.assertEqual(client.request("on", runtime)["mode"], "control")
                    self.assertEqual(len(list(Path(f"/proc/{server.pid}/task").iterdir())), 1)
                    denied_code = """
import importlib.machinery, importlib.util, json, pathlib, sys
loader = importlib.machinery.SourceFileLoader("client", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
print(json.dumps(module.request("off", pathlib.Path(sys.argv[2]))))
"""
                    denied = subprocess.run(
                        [sys.executable, "-B", "-c", denied_code,
                         str(ASSETS / "gcs-student-control"), str(runtime)],
                        capture_output=True, text=True, timeout=4,
                        user=nobody.pw_uid, group=nobody.pw_gid, extra_groups=[])
                    self.assertEqual(denied.returncode, 0, denied.stderr)
                    self.assertIn("error", json.loads(denied.stdout))
                    self.assertEqual(client.request("status", runtime)["mode"], "control")
                finally:
                    server.terminate()
                    server.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
