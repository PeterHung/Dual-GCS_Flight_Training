"""Exercise config updates and rollback using isolated files and fake networking."""
from copy import deepcopy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('update_manager', ROOT / 'mavlink/assets/manage.py')
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        m = self.m
        self.temp = tempfile.TemporaryDirectory(prefix='gcs-update-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('ETC', 'OPT', 'UNITS', 'NM_FILES', 'STATE', 'BACKUP_ROOT'):
            setattr(m, name, self.root / name.lower())
        m.MANIFEST = m.ETC / 'rf-bundle.json'
        m.ENV = m.ETC / 'gcs-mavproxy.env'
        m.STUDENT_CONFIG = m.ETC / 'student-relay.json'
        m.VENV = m.OPT / 'venv'
        m.SWITCH_CLI = self.root / 'student-control'
        m.LOCK_FILE = self.root / 'install.lock'
        self.cfg = m.load_config(ROOT / 'config.json')
        self.rf = str(uuid.uuid4())
        self.hotspot = str(uuid.uuid4())
        self.profiles = {
            self.rf: {'connection.interface-name': self.cfg['rf_interface'],
                      'ipv4.addresses': self.cfg['rf_address'], 'ipv4.routes': self.cfg['rf_peer'] + '/32'},
            self.hotspot: {'connection.interface-name': self.cfg['hotspot_interface'],
                           'ipv4.addresses': self.cfg['hotspot_address'],
                           '802-11-wireless.ssid': self.cfg['hotspot_ssid'], 'psk': 'unchanged-test-password'}}
        self.active = {self.rf, self.hotspot}
        self.units = dict.fromkeys((*m.SERVICES, 'NetworkManager'), True)
        self.calls = []
        self.fail_once = False
        self.record = {'version': 2, 'ready': True, 'origin': 'fresh', 'runtime_owned': False,
                       'config': self.cfg, 'account': 'user', 'profile': self.hotspot}
        for path, (body, mode) in m.payload(self.cfg, 'user').items():
            m.write(path, body, mode)
        m.manifest_write(self.record)
        for profile in self.profiles:
            self.persist(profile)
        self.initial = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        for context in (patch.object(m, 'run', self.fake_run), patch.object(os, 'chown', create=True),
                        patch.dict(os.environ, {'SSH_CONNECTION': ''}),
                        patch.dict(sys.modules, {'pwd': SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_gid=1000))})):
            context.start()
            self.addCleanup(context.stop)

    def persist(self, profile):
        settings = self.profiles[profile]
        path = self.m.NM_FILES / (profile + '.nmconnection')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist fields in an ordinary keyfile so rollback must restore real bytes.
        path.write_text('[connection]\nuuid=' + profile + '\n[test]\n' +
                        '\n'.join(f'{key}={value}' for key, value in settings.items()) + '\n')

    def fake_run(self, *args, **kwargs):
        args = tuple(str(arg) for arg in args)
        self.calls.append(args)
        m = self.m
        def result(text='', code=0):
            return SimpleNamespace(stdout=text, stderr='', returncode=code)
        if args[0] == 'systemctl':
            action = args[1]
            if action == 'is-active':
                return result(code=0 if self.units.get(args[-1], False) else 3)
            if action == 'restart' and args[-1] == m.PROXY and self.fail_once:
                self.fail_once = False
                raise RuntimeError('simulated update startup failure')
            if action in ('start', 'restart', 'stop'):
                for unit in args[2:]:
                    self.units[unit] = action != 'stop'
                    if unit == m.HOTSPOT_UNIT and action != 'stop':
                        self.active.add(self.hotspot)
            return result()
        if args[0] == str(m.SWITCH_CLI):
            return result('{"mode":"monitor"}')
        if args[0] == 'ss':
            return result('mavproxy.py' if self.units[m.PROXY] else '')
        if args[0] == 'ip':
            rf = self.profiles[self.rf]
            address = ipaddress.IPv4Interface(rf['ipv4.addresses'])
            if 'address' in args:
                return result(json.dumps([{'addr_info': [{'local': str(address.ip),
                    'prefixlen': address.network.prefixlen, 'valid_life_time': 4294967295}]}]))
            if args[-1] == 'default':
                return result('[]')
            return result(json.dumps([{'dev': rf['connection.interface-name'], 'prefsrc': str(address.ip)}]))
        if args[0] == 'nmcli':
            if 'GENERAL.TYPE' in args:
                return result('ethernet' if args[-1] in ('gcsrf0', 'gcsrf1') else 'wifi')
            if 'GENERAL.CON-UUID' in args:
                return result(self.rf)
            if 'NAME,UUID' in args:
                return result(m.HOTSPOT + ':' + self.hotspot)
            if '--active' in args:
                return result('\n'.join(sorted(self.active)))
            if 'ipv4.method' in args and 'modify' not in args:
                return result('manual')
            if 'modify' in args:
                profile = args[4]
                self.profiles[profile].update(zip(args[5::2], args[6::2]))
                self.persist(profile)
            elif 'reload' in args:
                import configparser
                for profile in self.profiles:
                    settings = configparser.ConfigParser(interpolation=None)
                    settings.read(m.NM_FILES / (profile + '.nmconnection'))
                    self.profiles[profile] = dict(settings['test'])
            elif 'up' in args or 'down' in args:
                profile = args[args.index('uuid') + 1]
                if 'up' in args:
                    self.active.add(profile)
                else:
                    self.active.discard(profile)
            return result()
        raise AssertionError(args)

    def changed_network(self):
        cfg = deepcopy(self.cfg)
        cfg.update(rf_address='192.168.168.248/24', rf_peer='192.168.168.197',
                   hotspot_address='10.43.0.1/24', hotspot_ssid='GCS_New', student_hotspot_ip='10.43.0.210')
        return cfg

    def test_student_change_updates_runtime_and_manifest_without_network_changes(self):
        cfg = deepcopy(self.cfg)
        cfg['student_hotspot_ip'] = '10.42.0.210'
        self.m.update(cfg, 'user', self.record)
        self.assertEqual(json.loads(self.m.STUDENT_CONFIG.read_text())['student_ip'], '10.42.0.210')
        self.assertEqual(self.m.manifest_read()['config'], cfg)
        self.assertFalse(any(call[:3] == ('nmcli', 'connection', 'modify') for call in self.calls))
        self.assertIn(('systemctl', 'restart', self.m.SWITCH), self.calls)
        self.assertFalse(any(call[0] == str(self.m.SWITCH_CLI) and call[-1] in ('on', 'off') for call in self.calls))

    def test_network_change_preserves_hotspot_password_and_updates_rf_route(self):
        cfg = self.changed_network()
        self.m.update(cfg, 'user', self.record)
        self.assertEqual(self.profiles[self.rf]['ipv4.addresses'], cfg['rf_address'])
        self.assertEqual(self.profiles[self.rf]['ipv4.routes'], cfg['rf_peer'] + '/32')
        self.assertEqual(self.profiles[self.hotspot]['ipv4.addresses'], cfg['hotspot_address'])
        self.assertEqual(self.profiles[self.hotspot]['psk'], 'unchanged-test-password')
        self.assertEqual(self.m.manifest_read()['profile'], self.hotspot)

    def test_failed_network_update_restores_files_profiles_and_running_services(self):
        self.fail_once = True
        with self.assertRaisesRegex(RuntimeError, 'simulated update startup failure'):
            self.m.update(self.changed_network(), 'user', self.record)
        for path, original in self.initial.items():
            self.assertEqual(path.read_bytes(), original, str(path))
        self.assertEqual(self.profiles[self.rf]['ipv4.addresses'], self.cfg['rf_address'])
        self.assertEqual(self.profiles[self.hotspot]['ipv4.addresses'], self.cfg['hotspot_address'])
        self.assertTrue(all(self.units[unit] for unit in self.m.SERVICES))
        self.assertTrue(list(self.m.BACKUP_ROOT.glob('*/files.json')))

    def test_unchanged_install_does_not_restart_or_create_backup(self):
        self.m.update(self.cfg, 'user', self.record)
        self.assertFalse(any(call[:2] == ('systemctl', 'restart') for call in self.calls))
        self.assertFalse(self.m.BACKUP_ROOT.exists())

    def test_installed_install_cli_reads_edited_root_config(self):
        cfg = deepcopy(self.cfg)
        cfg['student_hotspot_ip'] = '10.42.0.211'
        config = self.root / 'config.json'
        config.write_text(json.dumps(cfg))
        with patch.object(sys, 'argv', ['manage.py', 'install', '--user', 'user', '--config', str(config)]), \
             patch.object(os, 'geteuid', return_value=0, create=True):
            self.m.main()
        self.assertEqual(json.loads(self.m.STUDENT_CONFIG.read_text())['student_ip'], '10.42.0.211')


if __name__ == '__main__':
    unittest.main()
