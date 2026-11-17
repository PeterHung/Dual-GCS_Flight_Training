#!/usr/bin/env python3
"""Read the single editable bundle configuration; never change host settings."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config.json'


def load_config(path=DEFAULT_CONFIG):
    # Windows editors may save UTF-8 with BOM.
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    for key in ('rf_interface', 'hotspot_interface'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', cfg[key]):
            raise ValueError(f'Invalid {key}')
    if cfg['rf_interface'] == cfg['hotspot_interface']:
        raise ValueError('RF and Hotspot must use different interfaces')
    rf = ipaddress.IPv4Interface(cfg['rf_address'])
    peer = ipaddress.IPv4Address(cfg['rf_peer'])
    hotspot = ipaddress.IPv4Interface(cfg['hotspot_address'])
    student = ipaddress.IPv4Address(cfg['student_hotspot_ip'])
    for network, address, label in ((rf, peer, 'RF peer'), (hotspot, student, 'Student')):
        if (address not in network.network or address in
                (network.ip, network.network.network_address, network.network.broadcast_address)
                or address.is_multicast or address.is_unspecified):
            raise ValueError(f'{label} must have a different usable address on its subnet')
        if network.ip.is_multicast or network.ip.is_unspecified or network.ip.is_loopback:
            raise ValueError(f'{label} interface must have a usable unicast address')
    if rf.network.overlaps(hotspot.network):
        raise ValueError('RF and Hotspot subnets overlap')
    if cfg['boot_default'] not in ('control', 'monitor'):
        raise ValueError('Invalid boot_default')
    ssid = cfg['hotspot_ssid']
    if not ssid.isprintable() or not 1 <= len(ssid.encode('utf-8')) <= 32:
        raise ValueError('Invalid Hotspot SSID')
    video = cfg.get('video', {})
    if not isinstance(video, dict):
        raise ValueError('video must be an object')
    port = video.get('port', 8554)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('video.port must be an integer between 1024 and 65535')
    if video.get('codec', 'h265') not in ('h264', 'h265'):
        raise ValueError('video.codec must be h264 or h265')
    uri = video.get('upstream_url', '')
    if not isinstance(uri, str):
        raise ValueError('video.upstream_url must be a string')
    if uri:
        validate_upstream(uri, str(hotspot.ip), port)
    return cfg


def validate_upstream(uri, address, port):
    endpoint = urlsplit(uri)
    if endpoint.scheme != 'rtsp' or not endpoint.hostname:
        raise ValueError('Configure a valid physical camera RTSP upstream_url')
    if endpoint.hostname == address and (endpoint.port or 554) == port:
        raise ValueError('The camera upstream_url must not point to the relay itself')


def video_settings(cfg):
    video = cfg.get('video', {})
    return {'listen_address': str(ipaddress.IPv4Interface(cfg['hotspot_address']).ip),
            'port': video.get('port', 8554), 'mount': '/camera',
            'codec': video.get('codec', 'h265')}


def video_url(cfg):
    video = video_settings(cfg)
    return f"rtsp://{video['listen_address']}:{video['port']}{video['mount']}"
