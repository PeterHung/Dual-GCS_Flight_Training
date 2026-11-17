#!/usr/bin/env python3
"""Install and manage the optional GCS video relay independently of MAVLink."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ASSETS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ASSETS.parent / 'tools'))
from configuration import DEFAULT_CONFIG, load_config, validate_upstream, video_settings, video_url
from deployment_backup import Backup, BACKUP_ROOT
PROGRAM = Path('/opt/gcs/video-relay.py')
CONFIG = Path('/etc/gcs/video-relay.json')
UNIT = Path('/etc/systemd/system/gcs-video-relay.service')
SERVICE = UNIT.name


def run(*args, check=True):
    return subprocess.run([str(arg) for arg in args], check=check)


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(text.encode()); f.close()
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def install(account, config_path=DEFAULT_CONFIG):
    import grp
    import pwd

    bundle = load_config(config_path)
    user = pwd.getpwnam(account)
    if user.pw_uid == 0:
        raise ValueError('Choose the desktop QGC user, not root')
    source = str(Path(user.pw_dir) / '.config/QGroundControl/QGroundControl.ini')
    # Validate QGC settings without printing the camera URL or changing QGC.
    import importlib.util
    spec = importlib.util.spec_from_file_location('video_relay', ASSETS / 'video-relay.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    previous = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    uri = (bundle.get('video', {}).get('upstream_url') or previous.get('upstream_url')
           or module.source_url(source))
    cfg = {**video_settings(bundle), 'qgc_settings': source, 'upstream_url': uri}
    validate_upstream(uri, cfg['listen_address'], cfg['port'])
    if previous:
        validate_upstream(uri, previous['listen_address'], previous['port'])
    check = subprocess.run(['/usr/bin/python3', '-c',
        'import gi;gi.require_version("Gst","1.0");gi.require_version("GstRtspServer","1.0");'
        'from gi.repository import Gst,GstRtspServer;Gst.init(None);'
        'assert all(Gst.ElementFactory.find(n) for n in ' +
        repr(('rtspsrc', f"rtp{cfg['codec']}depay", f"{cfg['codec']}parse", f"rtp{cfg['codec']}pay")) + ')'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        run('apt-get', 'install', '-y', '--no-install-recommends',
            'python3-gi', 'gir1.2-gst-rtsp-server-1.0',
            'gstreamer1.0-plugins-good', 'gstreamer1.0-plugins-bad')
    group = grp.getgrgid(user.pw_gid).gr_name
    template = (ASSETS / 'gcs-video-relay.service.in').read_text()
    files = {PROGRAM: ((ASSETS / 'video-relay.py').read_text(), 0o755),
             CONFIG: (json.dumps(cfg, indent=2) + '\n', 0o640),
             UNIT: (template.replace('__SERVICE_USER__', user.pw_name)
                    .replace('__SERVICE_GROUP__', group), 0o644)}
    if all(path.is_file() and path.read_text() == body for path, (body, _mode) in files.items()):
        run('systemctl', 'enable', '--now', SERVICE)
        print(f'Video configuration already current. QGC URL: {video_url(bundle)}')
        return
    backup = Backup(files, 'video', BACKUP_ROOT)
    was_installed = UNIT.exists()
    was_active = run('systemctl', 'is-active', '--quiet', SERVICE, check=False).returncode == 0
    print(f'Video configuration backup: {backup.directory}', flush=True)
    try:
        if was_installed:
            run('systemctl', 'stop', SERVICE)
        for path, (body, mode) in files.items():
            write(path, body, mode)
        os.chown(CONFIG, 0, user.pw_gid)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', SERVICE)
        run('systemctl', 'is-active', '--quiet', SERVICE)
        run('systemctl', 'enable', SERVICE)
    except (Exception, KeyboardInterrupt):
        try:
            run('systemctl', 'stop', SERVICE, check=False)
            if not was_installed:
                run('systemctl', 'disable', SERVICE, check=False)
            backup.restore()
            run('systemctl', 'daemon-reload')
            if was_active:
                run('systemctl', 'start', SERVICE)
        except Exception as error:
            print(f'Video rollback incomplete. Backup: {backup.directory}; {error}', file=sys.stderr)
        raise
    print(f'Video installed/updated. QGC URL: {video_url(bundle)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'start', 'stop', 'remove', 'status'])
    parser.add_argument('--user', default=os.environ.get('SUDO_USER', 'user'))
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if os.geteuid() != 0 and args.action != 'status':
        parser.error('Run with sudo')
    if args.action == 'install':
        import fcntl
        with Path('/run/lock/gcs-video-install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            install(args.user, args.config)
    elif args.action == 'start':
        run('systemctl', 'start', SERVICE)
    elif args.action == 'stop':
        run('systemctl', 'stop', SERVICE)
    elif args.action == 'status':
        run('systemctl', 'status', '--no-pager', SERVICE)
    elif args.action == 'remove':
        if UNIT.exists():
            run('systemctl', 'disable', '--now', SERVICE)
        for path in (UNIT, CONFIG, PROGRAM):
            path.unlink(missing_ok=True)
        run('systemctl', 'daemon-reload')
        print('Removed video relay. GStreamer packages and MAVLink services retained.')


if __name__ == '__main__':
    main()
