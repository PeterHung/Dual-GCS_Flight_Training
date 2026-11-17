#!/usr/bin/env python3
"""Exercise the relay with real loopback UDP and credential-checked Unix sockets."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest


ASSETS = Path(__file__).resolve().parents[1] / "assets"
SPEC = importlib.util.spec_from_file_location("student_relay", ASSETS / "student-relay.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="gcs-relay-")
        self.addCleanup(self.directory.cleanup)
        self.runtime = Path(self.directory.name)
        self.cfg = {"hotspot_ip": "127.0.0.1", "student_ip": "127.0.0.1",
                    "boot_default": "control"}
        self.master = self.udp_socket()
        self.student = self.udp_socket()
        self.client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.client.bind(str(self.runtime / "client"))
        self.client.settimeout(0.3)
        self.addCleanup(self.client.close)
        self.relay = self.new_relay()
        self.addCleanup(lambda: self.relay.close())

    def udp_socket(self):
        connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        connection.bind(("127.0.0.1", 0))
        connection.settimeout(0.05)
        self.addCleanup(connection.close)
        return connection

    def new_relay(self, uid=None):
        return MODULE.StudentRelay(self.cfg, self.runtime, telemetry_port=0, student_port=0,
                                   qgc_port=self.student.getsockname()[1],
                                   control_uid=os.getuid() if uid is None else uid)

    def command(self, action):
        self.client.sendto(action, str(self.relay.control_path))
        self.relay.step(0.1)
        return json.loads(self.client.recv(8192))

    def telemetry(self, payload=b"vehicle telemetry"):
        self.master.sendto(payload, self.relay.telemetry.getsockname())
        self.relay.step(0.1)
        return self.student.recvfrom(65535)

    def student_command(self, payload=b"parameter read"):
        self.student.sendto(payload, self.relay.student.getsockname())
        self.relay.step(0.1)

    def test_bidirectional_and_monitor_telemetry(self):
        data, sender = self.telemetry()
        self.assertEqual(data, b"vehicle telemetry")
        self.assertEqual(sender, self.relay.student.getsockname())
        self.student_command()
        self.assertEqual(self.master.recv(65535), b"parameter read")
        self.assertEqual(self.command(b"off")["mode"], "monitor")
        self.student_command()
        with self.assertRaises(socket.timeout):
            self.master.recv(65535)
        self.assertEqual(self.telemetry(b"telemetry during off")[0], b"telemetry during off")
        state = self.command(b"status")
        self.assertEqual(state["student_forwarded"], 1)
        self.assertEqual(state["student_dropped"], 1)
        self.assertEqual(self.command(b"on")["mode"], "control")
        self.student_command(b"new request")
        self.assertEqual(self.master.recv(65535), b"new request")

    def test_queued_requests_are_dropped_on_enable(self):
        self.telemetry()
        self.command(b"off")
        self.student.sendto(b"stale request", self.relay.student.getsockname())
        self.assertEqual(self.command(b"on")["mode"], "control")
        with self.assertRaises(socket.timeout):
            self.master.recv(65535)
        self.student_command(b"fresh request")
        self.assertEqual(self.master.recv(65535), b"fresh request")

    def test_connected_udp_rejects_other_sender(self):
        self.telemetry()
        other = self.udp_socket()
        other.sendto(b"unregistered source", self.relay.student.getsockname())
        self.relay.step(0.02)
        with self.assertRaises(socket.timeout):
            self.master.recv(65535)
        self.assertEqual(self.relay.counts["student_forwarded"], 0)

    def test_control_requires_matching_kernel_credentials(self):
        self.command(b"off")
        self.relay.close()
        self.relay = self.new_relay(os.getuid() + 1)
        self.assertIn("error", self.command(b"on"))
        self.assertEqual(self.relay.mode, "monitor")

    def test_invalid_control_message_is_rejected(self):
        self.assertIn("error", self.command(b"unexpected"))
        self.assertIn("error", self.command(b"\xff"))
        self.assertEqual(self.relay.mode, "control")

    def test_monitor_survives_process_restart(self):
        self.command(b"off")
        self.relay.close()
        self.relay = self.new_relay()
        self.assertEqual(self.command(b"status")["mode"], "monitor")
        self.assertEqual(self.telemetry()[0], b"vehicle telemetry")

    def test_boot_default_applies_without_runtime_state(self):
        self.command(b"off")
        self.relay.close()
        (self.runtime / "mode.json").unlink()
        self.relay = self.new_relay()
        self.assertEqual(self.relay.mode, "control")

    def test_single_thread(self):
        self.telemetry()
        self.command(b"off")
        self.assertEqual(len(list(Path("/proc/self/task").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
