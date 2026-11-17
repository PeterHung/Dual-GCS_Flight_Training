#!/usr/bin/env python3
"""Check package files and exercise orchestration with isolated fake services."""
import ast
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True


def run(*args, env=None, check=True):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          timeout=90, env=env, check=check)


def mock_flow():
    if os.geteuid() != 0:
        return {'status': 'skipped', 'reason': 'Run as root for shell entry-point checks'}
    with tempfile.TemporaryDirectory(prefix='gcs-bundle-check-') as temporary:
        site = Path(temporary)
        (site / 'tools').mkdir()
        (site / 'bin').mkdir()
        manifest = site / 'fake-manifest.json'
        manifest.write_text('{}')
        (site / 'tools/common.sh').write_bytes((ROOT / 'tools/common.sh').read_bytes())
        (site / 'tools/check_config.py').write_text('print("mock config passed")\n')
        for source in ROOT.glob('0*.sh'):
            text = source.read_text().replace('/etc/gcs/rf-bundle.json', str(manifest))
            text = text.replace('/etc/systemd/system/', str(site / 'units') + '/')
            text = text.replace('/usr/local/sbin/gcs-student-control', str(site / 'no-cli'))
            text = text.replace('/run/lock/gcs-complete-install.lock', str(site / 'install.lock'))
            text = text.replace('/etc/gcs/video-relay.json', str(site / 'video-settings.json'))
            (site / source.name).write_text(text)
        for component in ['mavlink', 'video']:
            folder = site / component
            folder.mkdir()
            for name in ['01_install.sh', '02_start.sh', '03_stop.sh', '04_remove.sh']:
                step = component + ':' + name[:2]
                (folder / name).write_text(
                    '#!/usr/bin/env bash\nset -euo pipefail\n'
                    f'echo "{step}" >> "$GCS_TEST_LOG"\n'
                    f'if [[ "${{GCS_FAIL_STEP:-}}" == "{step}" ]]; then exit 9; fi\n')
        for name, body in {
            'systemctl': 'if [[ "$1" == show ]]; then echo loaded; fi\n',
            'nmcli': 'exit 0\n', 'ss': 'exit 0\n',
        }.items():
            path = site / 'bin' / name
            path.write_text('#!/usr/bin/env bash\n' + body)
            path.chmod(0o755)
        log = site / 'steps.txt'
        env = {**os.environ, 'PATH': str(site / 'bin') + ':' + os.environ['PATH'],
               'GCS_TEST_LOG': str(log), 'PYTHONDONTWRITEBYTECODE': '1'}
        cases = [
            ('install', '01_install.sh', ['mavlink:01', 'video:01'], None),
            ('start', '02_start.sh', ['mavlink:02', 'video:02'], None),
            ('stop', '03_stop.sh', ['video:03', 'mavlink:03'], None),
            ('remove', '04_remove.sh', ['video:04', 'mavlink:04'], None),
            ('video_install_failure', '01_install.sh', ['mavlink:01', 'video:01'], 'video:01'),
            ('video_remove_failure', '04_remove.sh', ['video:04'], 'video:04'),
            ('status', '05_status.sh', [], None),
        ]
        results = {}
        for label, script, expected, failure in cases:
            log.write_text('')
            current = {**env, 'GCS_FAIL_STEP': failure or ''}
            result = run('bash', site / script, env=current, check=False)
            observed = log.read_text().splitlines()
            assert observed == expected, (label, observed, expected)
            assert (result.returncode != 0) == bool(failure), (label, result.stdout, result.stderr)
            results[label] = 'passed'
        return {'status': 'passed', 'cases': results,
                'scope': 'Temporary directory and fake services; no real units or networking touched'}


def main():
    report = {'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
              'live_gcs_modified': False}
    python_files = sorted(ROOT.rglob('*.py'))
    json_files = sorted(ROOT.rglob('*.json'))
    shell_files = sorted(ROOT.rglob('*.sh'))
    for path in python_files:
        ast.parse(path.read_text(encoding='utf-8'))
    for path in json_files:
        json.loads(path.read_text(encoding='utf-8-sig'))
    for path in shell_files:
        assert b'\r' not in path.read_bytes(), path
        run('bash', '-n', path)
    report['python_syntax'] = {'passed': len(python_files)}
    report['json_parse'] = {'passed': len(json_files)}
    report['shell_syntax_and_lf'] = {'passed': len(shell_files)}
    run(sys.executable, '-B', ROOT / 'tools/check_config.py')
    run(sys.executable, '-B', ROOT / 'mavlink/assets/manage.py', 'check')
    assert run('bash', ROOT / '01_install.sh', '--help').returncode == 0
    assert run('bash', ROOT / '01_install.sh', '--invalid', check=False).returncode == 2
    report['configuration_and_cli'] = 'passed'
    report['unit_tests'] = {}
    for directory in ('mavlink/tests', 'video/tests', 'tools/tests'):
        result = run(sys.executable, '-B', '-m', 'unittest', 'discover', '-s', ROOT / directory, '-v')
        count = re.search(r'Ran (\d+) tests?', result.stderr)
        report['unit_tests'][directory] = {'passed': int(count.group(1)),
            'skipped': len(re.findall(r'\.\.\. skipped ', result.stderr))}
    links = 0
    for path in ROOT.rglob('*.md'):
        text = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8'), flags=re.S)
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            relative = target.split('#', 1)[0].strip('<>')
            if relative:
                assert (path.parent / relative).exists(), (path, target)
                links += 1
    report['local_document_links'] = {'passed': links}
    for path in ROOT.rglob('*'):
        if path.is_file():
            assert path.relative_to(ROOT).as_posix().isascii(), path
            assert path.suffix not in ['.key', '.pyc', '.ini'], path
            assert path.name not in ['id_ed25519', 'video-relay.json'], path
            if path.suffix in ['.md', '.py', '.json', '.sh', '.in']:
                marker = b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'
                assert marker not in path.read_bytes(), path
    report['file_names_and_sensitive_file_exclusion'] = 'passed'
    report['orchestration'] = mock_flow()
    (ROOT / 'verification/package_checks.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
