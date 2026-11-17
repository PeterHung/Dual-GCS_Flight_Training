#!/usr/bin/env python3
"""Probe bidirectional MAVLink through the GCS hotspot, using only the stdlib."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import struct
import sys
import time

CRC_EXTRA = {0: 50, 20: 214, 22: 220}

def crc_x25(data):
    crc = 0xFFFF
    for value in data:
        tmp = value ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc

def request_parameter(sequence, system_id, component_id, parameter):
    payload = struct.pack("<hBB16s", -1, system_id, component_id,
                          parameter.encode("ascii").ljust(16, b"\0"))
    header = bytes((0xFD, len(payload), 0, 0, sequence & 255, 253, 190, 20, 0, 0))
    crc = crc_x25(header[1:] + payload + bytes((CRC_EXTRA[20],)))
    return header + payload + struct.pack("<H", crc)

def frames(datagram):
    offset = 0
    while offset < len(datagram):
        magic = datagram[offset]
        if magic not in (0xFD, 0xFE):
            offset += 1
            continue
        header_size = 10 if magic == 0xFD else 6
        if offset + header_size > len(datagram):
            break
        size = datagram[offset + 1]
        signature_size = 13 if magic == 0xFD and datagram[offset + 2] & 1 else 0
        end = offset + header_size + size + 2 + signature_size
        if end > len(datagram):
            offset += 1
            continue
        frame = datagram[offset:end]
        if magic == 0xFD:
            msgid = int.from_bytes(frame[7:10], "little")
            system, component = frame[5], frame[6]
        else:
            msgid, system, component = frame[5], frame[3], frame[4]
        payload = frame[header_size:header_size + size]
        valid_crc = None
        if msgid in CRC_EXTRA:
            expected = struct.unpack_from("<H", frame, header_size + size)[0]
            valid_crc = crc_x25(frame[1:header_size + size] +
                               bytes((CRC_EXTRA[msgid],))) == expected
        yield msgid, system, component, payload, valid_crc
        offset = end

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcs-ip", default="10.42.0.1")
    parser.add_argument("--listen-port", type=int, default=14550)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--param", default="SYS_AUTOSTART")
    parser.add_argument("--report", type=Path,
                        default=Path.cwd() / "Windows_RF_Probe.json")
    args = parser.parse_args()
    if len(args.param.encode("ascii")) > 16:
        parser.error("--param must be at most 16 ASCII characters")
    report = {
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "gcs_ip": args.gcs_ip, "listen_port": args.listen_port,
        "probe_system_id": 253, "requested_parameter": args.param,
        "operation": "HEARTBEAT receive and PARAM_REQUEST_READ only",
        "status": "starting", "requests_sent": 0,
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("0.0.0.0", args.listen_port))
    except OSError as exc:
        report.update(status="bind_failed", error=str(exc), winerror=getattr(exc, "winerror", None))
        sock.close()
        return finish(args.report, report, 3)
    sock.settimeout(0.25)
    counts = Counter()
    invalid_crc = 0
    target = None
    last_request = None
    request_start = None
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if target and report["requests_sent"] < 4 and (
                last_request is None or now - last_request >= 2
            ):
                packet = request_parameter(report["requests_sent"], target[0], target[1], args.param)
                sock.sendto(packet, target[2])
                report["requests_sent"] += 1
                last_request = now
                if request_start is None:
                    request_start = now
            try:
                datagram, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            if address[0] != args.gcs_ip:
                continue
            report["last_sender"] = list(address)
            for msgid, system, component, payload, valid in frames(datagram):
                if valid is False:
                    invalid_crc += 1
                    continue
                counts[msgid] += 1
                if msgid == 0 and valid and len(payload) >= 9 and payload[5] != 8:
                    target = (system, component, address)
                    report["heartbeat"] = {
                        "system_id": system, "component_id": component,
                        "vehicle_type": payload[4], "autopilot": payload[5],
                        "base_mode": payload[6], "system_status": payload[7],
                        "sender": list(address), "crc_valid": True,
                    }
                if (msgid == 22 and valid and target and
                        (system, component) == target[:2] and request_start is not None):
                    padded = payload.ljust(25, b"\0")
                    param_name = padded[8:24].split(b"\0", 1)[0].decode("ascii", "replace")
                    if param_name == args.param:
                        param_type = padded[24]
                        formats = {1:"<B",2:"<b",3:"<H",4:"<h",5:"<I",6:"<i",9:"<f"}
                        value = struct.unpack_from(formats[param_type], padded)[0] if param_type in formats else None
                        report["parameter_reply"] = {
                            "name": param_name, "type": param_type,
                            "value_bytewise": value, "raw_value_hex": padded[:4].hex(),
                            "system_id": system, "component_id": component,
                            "latency_ms": round((time.monotonic()-request_start)*1000, 2),
                            "crc_valid": True,
                        }
                        report["status"] = "bidirectional_ok"
                        return finish(args.report, {
                            **report, "message_counts": dict(sorted(counts.items())),
                            "invalid_known_crc": invalid_crc,
                        }, 0)
        report["status"] = "heartbeat_only" if "heartbeat" in report else "no_heartbeat"
        return finish(args.report, {
            **report, "message_counts": dict(sorted(counts.items())),
            "invalid_known_crc": invalid_crc,
        }, 2)
    finally:
        sock.close()

def finish(path, report, exit_code):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("REPORT:", path.resolve())
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
