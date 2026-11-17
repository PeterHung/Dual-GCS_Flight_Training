#!/usr/bin/env python3
"""Share the coach camera on the hotspot using RTSP/TCP without transcoding."""
import argparse
import configparser
import ipaddress
import json
from pathlib import Path
import signal
from urllib.parse import urlsplit


def source_url(path):
    settings = configparser.ConfigParser(interpolation=None)
    settings.optionxform = str
    if not settings.read(path):
        raise ValueError('Coach QGC settings file is unavailable')
    value = settings.get('Video', 'rtspUrl', fallback='').strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    parsed = urlsplit(value)
    if parsed.scheme != 'rtsp' or not parsed.hostname:
        raise ValueError('Configure a valid RTSP URL in the coach QGC first')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/gcs/video-relay.json')
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    address = str(ipaddress.IPv4Address(cfg['listen_address']))
    port = int(cfg.get('port', 8554))
    if not 1024 <= port <= 65535:
        raise ValueError('Use a nonprivileged TCP port')
    mount = cfg.get('mount', '/camera')
    if mount != '/camera':
        raise ValueError('This deployment uses the /camera mount')
    codec = cfg.get('codec', 'h265')
    if codec not in ('h264', 'h265'):
        raise ValueError('codec must be h264 or h265')
    uri = cfg.get('upstream_url') or source_url(cfg['qgc_settings'])
    upstream = urlsplit(uri)
    if upstream.scheme != 'rtsp' or not upstream.hostname:
        raise ValueError('Invalid upstream RTSP URL')
    if upstream.hostname == address and (upstream.port or 554) == port:
        raise ValueError('The upstream must be the camera, not this relay')

    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstRtsp', '1.0')
    gi.require_version('GstRtspServer', '1.0')
    from gi.repository import GLib, Gst, GstRtsp, GstRtspServer

    Gst.init(None)
    for name in ('rtspsrc', f'rtp{codec}depay', f'{codec}parse', f'rtp{codec}pay'):
        if not Gst.ElementFactory.find(name):
            raise RuntimeError(f'Missing GStreamer element: {name}')
    factory = GstRtspServer.RTSPMediaFactory.new()
    factory.set_launch(
        f'( rtspsrc name=upstream protocols=tcp latency=100 tcp-timeout=5000000 '
        f'! rtp{codec}depay ! {codec}parse '
        f'! rtp{codec}pay name=pay0 pt=96 config-interval=1 )')
    factory.set_shared(True)
    factory.set_protocols(GstRtsp.RTSPLowerTrans.TCP)
    factory.set_latency(100)
    factory.set_stop_on_disconnect(False)
    factory.set_eos_shutdown(True)

    def configure_media(_factory, media):
        # Set credentials as a property, never interpolate them into a launch string.
        upstream = media.get_element().get_by_name('upstream')
        upstream.set_property('location', uri)

    factory.connect('media-configure', configure_media)
    server = GstRtspServer.RTSPServer.new()
    server.set_address(address)
    server.set_service(str(port))
    server.get_mount_points().add_factory(mount, factory)

    def viewer_connected(_server, client):
        peer = client.get_connection().get_ip()
        print(f'Viewer connected: {peer}', flush=True)
        client.connect('closed', lambda _client: print(f'Viewer disconnected: {peer}', flush=True))

    server.connect('client-connected', viewer_connected)
    loop = GLib.MainLoop()
    state = {'source_id': 0}

    def attach():
        try:
            state['source_id'] = server.attach(None)
        except GLib.Error:
            state['source_id'] = 0
        if state['source_id']:
            print(f'Ready: rtsp://{address}:{port}{mount}; codec={codec}; TCP only', flush=True)
            return GLib.SOURCE_REMOVE
        print(f'Waiting for hotspot address {address} or available port {port}', flush=True)
        return GLib.SOURCE_CONTINUE

    if attach() == GLib.SOURCE_CONTINUE:
        GLib.timeout_add_seconds(5, attach)

    def cleanup():
        server.get_session_pool().cleanup()
        return GLib.SOURCE_CONTINUE

    def stop():
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(10, cleanup)
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
    loop.run()
    if state['source_id']:
        GLib.source_remove(state['source_id'])


if __name__ == '__main__':
    main()
