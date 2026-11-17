#!/usr/bin/env python3
"""Single-threaded UDP relay with a local, credential-checked control socket."""

import argparse
from contextlib import ExitStack
import ipaddress
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import time


CONFIG = Path("/etc/gcs/student-relay.json")
RUNTIME = Path("/run/gcs-student-relay")
TELEMETRY_PORT = 14552
STUDENT_PORT = 14553
QGC_PORT = 14550
BATCH_SIZE = 64


def load_config(path):
    cfg = json.loads(path.read_text())
    cfg["hotspot_ip"] = str(ipaddress.IPv4Address(cfg["hotspot_ip"]))
    cfg["student_ip"] = str(ipaddress.IPv4Address(cfg["student_ip"]))
    if cfg["boot_default"] not in ("control", "monitor"):
        raise ValueError("Invalid boot_default")
    return cfg


class StudentRelay:
    def __init__(self, cfg, runtime, telemetry_port=TELEMETRY_PORT,
                 student_port=STUDENT_PORT, qgc_port=QGC_PORT, control_uid=0):
        self.runtime = Path(runtime)
        self.runtime.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.control_path = self.runtime / "control.sock"
        self.state_path = self.runtime / "mode.json"
        self.mode = cfg["boot_default"]
        if self.state_path.exists():
            self.mode = json.loads(self.state_path.read_text())["mode"]
        if self.mode not in ("control", "monitor"):
            raise ValueError("Invalid saved mode")
        self.control_uid = control_uid
        self.master_endpoint = None
        self.running = True
        self.started = time.monotonic()
        self.counts = dict(telemetry_forwarded=0, student_forwarded=0,
                           student_dropped=0, send_errors=0)
        self.resources = ExitStack()
        try:
            self.selector = self.resources.enter_context(selectors.DefaultSelector())
            self.telemetry = self.resources.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            self.telemetry.bind(("127.0.0.1", telemetry_port))
            self.student = self.resources.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            self.student.bind((cfg["hotspot_ip"], student_port))
            self.student.connect((cfg["student_ip"], qgc_port))
            self.control = self.resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
            self.control.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.control_path.unlink(missing_ok=True)
            self.control.bind(str(self.control_path))
            self.control_path.chmod(0o600)
            for connection, handler in ((self.telemetry, self.read_telemetry),
                                        (self.student, self.read_student),
                                        (self.control, self.read_control)):
                connection.setblocking(False)
                self.selector.register(connection, selectors.EVENT_READ, handler)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.resources.close()
        self.control_path.unlink(missing_ok=True)

    def send(self, connection, payload, destination=None):
        try:
            if destination is None:
                connection.send(payload)
            else:
                connection.sendto(payload, destination)
            return True
        except OSError:
            self.counts["send_errors"] += 1
            return False

    def read_telemetry(self):
        for _ in range(BATCH_SIZE):
            try:
                payload, sender = self.telemetry.recvfrom(65535)
            except BlockingIOError:
                return
            except OSError:
                self.counts["send_errors"] += 1
                return
            if not payload:
                continue
            self.master_endpoint = sender
            if self.send(self.student, payload):
                self.counts["telemetry_forwarded"] += 1

    def read_student(self):
        for _ in range(BATCH_SIZE):
            try:
                payload = self.student.recv(65535)
            except BlockingIOError:
                return
            except OSError:
                self.counts["send_errors"] += 1
                return
            if self.mode == "control" and self.master_endpoint and payload:
                if self.send(self.telemetry, payload, self.master_endpoint):
                    self.counts["student_forwarded"] += 1
            else:
                self.counts["student_dropped"] += 1

    def status(self):
        return {"mode": self.mode, "student_control_allowed": self.mode == "control",
                "master_endpoint": self.master_endpoint, "pid": os.getpid(),
                "uptime_seconds": round(time.monotonic() - self.started, 1), **self.counts}

    def set_mode(self, mode):
        # Drain old student packets before allowing a new control session.
        if mode == "control":
            for _ in range(4096):
                try:
                    self.student.recv(65535)
                except BlockingIOError:
                    break
                except OSError:
                    break
                self.counts["student_dropped"] += 1
            else:
                raise ValueError("Student receive queue is busy. Retry on.")
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": mode}) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
        self.mode = mode

    def read_control(self):
        for _ in range(BATCH_SIZE):
            try:
                payload, ancillary, flags, sender = self.control.recvmsg(1024, socket.CMSG_SPACE(12))
            except BlockingIOError:
                return
            uid = None
            for level, kind, value in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) >= 12:
                    _, uid, _ = struct.unpack("3i", value[:12])
            response = {}
            try:
                if uid != self.control_uid or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                    raise ValueError("Control requires a local root process")
                action = payload.decode("ascii")
                if action in ("on", "off"):
                    self.set_mode("control" if action == "on" else "monitor")
                elif action != "status":
                    raise ValueError("Unknown action")
                response = self.status()
            except (UnicodeError, ValueError, OSError) as error:
                response = {"error": str(error)}
            if sender:
                self.send(self.control, json.dumps(response).encode(), sender)

    def step(self, timeout=0.5):
        ready = self.selector.select(timeout)
        # Process mode changes before packets from the same select cycle.
        ready.sort(key=lambda event: event[0].fileobj is not self.control)
        for key, _ in ready:
            key.data()

    def serve(self):
        try:
            while self.running:
                self.step()
        finally:
            self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    import fcntl

    RUNTIME.mkdir(mode=0o750, parents=True, exist_ok=True)
    with (RUNTIME / "relay.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        relay = StudentRelay(load_config(args.config), RUNTIME)
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: setattr(relay, "running", False))
        relay.serve()


if __name__ == "__main__":
    main()
