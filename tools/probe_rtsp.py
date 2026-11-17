#!/usr/bin/env python3
"""Check RTSP/TCP playback from the student without sending flight commands."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import time
from urllib.parse import urljoin, urlsplit


def probe(uri, seconds):
    target = urlsplit(uri)
    if target.scheme != 'rtsp' or not target.hostname:
        raise ValueError('Use an RTSP URL')
    result = {'url': uri, 'status_codes': {}, 'rtp_packets': 0, 'rtp_bytes': 0,
              'channels': Counter()}
    with socket.create_connection((target.hostname, target.port or 554), timeout=5) as sock:
        sock.settimeout(15)
        result['local_endpoint'] = list(sock.getsockname())
        pending = bytearray()
        sequence = 0

        def receive(n):
            while len(pending) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError('RTSP server closed the connection')
                pending.extend(chunk)

        def interleaved():
            receive(4)
            size = int.from_bytes(pending[2:4], 'big')
            channel = pending[1]
            receive(4 + size)
            packet = bytes(pending[4:4 + size])
            del pending[:4 + size]
            result['channels'][channel] += 1
            if channel == 0 and len(packet) >= 12 and packet[0] >> 6 == 2:
                result['rtp_packets'] += 1
                result['rtp_bytes'] += len(packet)

        def response():
            while True:
                receive(1)
                if pending[0] == 36:
                    interleaved()
                    continue
                while b'\r\n\r\n' not in pending:
                    receive(len(pending) + 1)
                head_end = pending.index(b'\r\n\r\n')
                lines = bytes(pending[:head_end]).decode(errors='replace').split('\r\n')
                headers = dict(line.split(':', 1) for line in lines[1:] if ':' in line)
                headers = {k.lower(): v.strip() for k, v in headers.items()}
                length = int(headers.get('content-length', '0'))
                receive(head_end + 4 + length)
                body = bytes(pending[head_end + 4:head_end + 4 + length])
                del pending[:head_end + 4 + length]
                return int(lines[0].split()[1]), headers, body

        def request(method, url, headers=''):
            nonlocal sequence
            sequence += 1
            sock.sendall((f'{method} {url} RTSP/1.0\r\nCSeq: {sequence}\r\n'
                          'User-Agent: GCS-ReadOnly-Video-Probe\r\n' + headers + '\r\n').encode())
            status, fields, body = response()
            result['status_codes'][method] = status
            if status != 200:
                raise RuntimeError(f'{method} returned {status}')
            return fields, body

        fields, body = request('DESCRIBE', uri, 'Accept: application/sdp\r\n')
        sdp = body.decode(errors='replace')
        result['h265_advertised'] = 'H265/90000' in sdp.upper()
        controls = [line[10:].strip() for line in sdp.splitlines()
                    if line.startswith('a=control:') and line[10:].strip() != '*']
        if not controls:
            raise RuntimeError('No video track in SDP')
        base = fields.get('content-base', uri.rstrip('/') + '/')
        track = controls[0] if controls[0].startswith('rtsp://') else urljoin(base, controls[0])
        setup, _ = request('SETUP', track, 'Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n')
        session = setup['session'].split(';', 1)[0]
        result['transport'] = setup.get('transport')
        request('PLAY', uri, f'Session: {session}\r\n')
        start = time.monotonic()
        sock.settimeout(2)
        while time.monotonic() - start < seconds:
            try:
                receive(1)
                if pending[0] == 36:
                    interleaved()
                else:
                    response()
            except socket.timeout:
                pass
        result['sample_seconds'] = round(time.monotonic() - start, 3)
        sequence += 1
        sock.sendall((f'TEARDOWN {uri} RTSP/1.0\r\nCSeq: {sequence}\r\n'
                      f'Session: {session}\r\n\r\n').encode())
        result['status'] = 'rtsp_tcp_video_received' if result['rtp_packets'] else 'no_rtp'
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='rtsp://10.42.0.1:8554/camera')
    p.add_argument('--seconds', type=float, default=5)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    record = {'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat()}
    try:
        record['initial'] = probe(args.url, args.seconds)
        record['reconnect'] = probe(args.url, 2)
        record['ok'] = all(record[k]['rtp_packets'] > 0 for k in ['initial', 'reconnect'])
    except Exception as error:
        record.update(ok=False, error=str(error))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2))
    raise SystemExit(0 if record['ok'] else 1)


if __name__ == '__main__':
    main()
