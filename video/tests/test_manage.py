"""Check camera config precedence, shared hotspot addressing and update rollback."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class VideoInstallTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('video_manager', ROOT / 'video/manage.py')
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.temp = tempfile.TemporaryDirectory(prefix='gcs-video-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.m.PROGRAM = self.root / 'program.py'
        self.m.CONFIG = self.root / 'private-video.json'
        self.m.UNIT = self.root / 'video.service'
        self.m.BACKUP_ROOT = self.root / 'backups'
        self.cfg = self.m.load_config(ROOT / 'config.json')
        self.config_file = self.root / 'config.json'
        self.previous = {'listen_address': '10.42.0.1', 'port': 8554, 'mount': '/camera',
                         'codec': 'h265', 'qgc_settings': str(self.root / '.config/QGroundControl/QGroundControl.ini'),
                         'upstream_url': 'rtsp://test:test-only@192.168.168.73:554/live'}
        self.m.CONFIG.write_text(json.dumps(self.previous))
        self.m.PROGRAM.write_text('previous program')
        self.m.UNIT.write_text('previous unit')
        self.initial = {path: path.read_bytes() for path in (self.m.PROGRAM, self.m.CONFIG, self.m.UNIT)}
        self.calls = []
        self.fail_once = False
        for context in (patch.object(self.m.subprocess, 'run', self.fake_run),
                        patch.object(os, 'chown', create=True),
                        patch.dict(sys.modules, {'pwd': SimpleNamespace(getpwnam=lambda _: SimpleNamespace(
                            pw_uid=1000, pw_gid=1000, pw_dir=str(self.root), pw_name='user')),
                            'grp': SimpleNamespace(getgrgid=lambda _: SimpleNamespace(gr_name='user'))})):
            context.start()
            self.addCleanup(context.stop)

    def fake_run(self, args, **kwargs):
        args = tuple(str(arg) for arg in args)
        self.calls.append(args)
        if args[:2] == ('systemctl', 'start') and self.fail_once:
            self.fail_once = False
            raise RuntimeError('simulated video start failure')
        return SimpleNamespace(returncode=0)

    def install(self):
        self.config_file.write_text(json.dumps(self.cfg))
        self.m.install('user', self.config_file)

    def test_hotspot_and_port_change_keep_saved_upstream(self):
        self.cfg.update(hotspot_address='10.43.0.1/24', student_hotspot_ip='10.43.0.210')
        self.cfg['video']['port'] = 8555
        self.install()
        actual = json.loads(self.m.CONFIG.read_text())
        self.assertEqual(actual['listen_address'], '10.43.0.1')
        self.assertEqual(actual['port'], 8555)
        self.assertEqual(actual['upstream_url'], self.previous['upstream_url'])

    def test_explicit_camera_url_and_codec_override_saved_values(self):
        self.cfg['video'].update(upstream_url='rtsp://192.168.168.80:554/live', codec='h264')
        self.install()
        actual = json.loads(self.m.CONFIG.read_text())
        self.assertEqual(actual['upstream_url'], self.cfg['video']['upstream_url'])
        self.assertEqual(actual['codec'], 'h264')
        self.assertTrue(any('rtph264depay' in call[-1] for call in self.calls if call[0] == '/usr/bin/python3'))

    def test_repeated_install_does_not_stop_video(self):
        self.install()
        backups = list(self.m.BACKUP_ROOT.iterdir())
        self.calls.clear()
        self.install()
        self.assertNotIn(('systemctl', 'stop', self.m.SERVICE), self.calls)
        self.assertEqual(list(self.m.BACKUP_ROOT.iterdir()), backups)

    def test_start_failure_restores_previous_program_settings_and_unit(self):
        self.fail_once = True
        with self.assertRaisesRegex(RuntimeError, 'simulated video start failure'):
            self.install()
        for path, original in self.initial.items():
            self.assertEqual(path.read_bytes(), original)

    def test_self_upstream_is_rejected_before_service_changes(self):
        self.cfg['video']['upstream_url'] = 'rtsp://10.42.0.1:8554/camera'
        with self.assertRaisesRegex(ValueError, 'relay itself'):
            self.install()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
