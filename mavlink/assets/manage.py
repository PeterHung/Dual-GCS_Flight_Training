#!/usr/bin/env python3
"""Manage the GCS RF forwarding stack on Ubuntu 24.04."""

import argparse
import ast
import configparser
from getpass import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


ASSETS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ASSETS.parent.parent / "tools"))
from configuration import DEFAULT_CONFIG, load_config
from deployment_backup import Backup, BACKUP_ROOT
ETC = Path("/etc/gcs")
OPT = Path("/opt/gcs")
UNITS = Path("/etc/systemd/system")
STATE = Path("/var/lib/gcs/mavproxy")
MANIFEST = ETC / "rf-bundle.json"
VENV = OPT / "venv"
HOTSPOT = "gcs-hotspot"
HOTSPOT_UNIT = f"{HOTSPOT}.service"
PROXY = "gcs-mavproxy.service"
SWITCH = "gcs-student-relay.service"
SERVICES = (PROXY, HOTSPOT_UNIT, SWITCH)
SWITCH_CLI = Path("/usr/local/sbin/gcs-student-control")
ENV = ETC / "gcs-mavproxy.env"
STUDENT_CONFIG = ETC / "student-relay.json"
NM_FILES = Path("/etc/NetworkManager/system-connections")
LOCK_FILE = Path("/run/lock/gcs-rf-bundle.lock")


def run(*args, allow_failure=False, timeout=90):
    process = subprocess.run([str(arg) for arg in args], text=True, capture_output=True,
                             timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    if process.returncode and not allow_failure:
        raise RuntimeError(f"{args[0]}: {process.stderr.strip() or process.stdout.strip()}")
    return process


def output(*args):
    return run(*args).stdout.strip()


def verify_runtime():
    run(VENV / "bin/python", "-c",
        "from importlib.metadata import version; import pymavlink.mavutil; "
        "import future; print(version('MAVProxy'))")


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content.encode("utf-8"))
            stream.close()
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def student_config(cfg):
    return {"boot_default": cfg["boot_default"],
            "hotspot_ip": str(ipaddress.IPv4Interface(cfg["hotspot_address"]).ip),
            "student_ip": cfg["student_hotspot_ip"]}


def payload(cfg, account):
    runtime = student_config(cfg)
    hotspot_service = asset(f"{HOTSPOT_UNIT}.in").replace(
        "__WIFI_INTERFACE__", cfg["hotspot_interface"]).replace("__HOTSPOT_CONNECTION_NAME__", HOTSPOT)
    env = (f"MAVPROXY_MASTER=udpin:{ipaddress.IPv4Interface(cfg['rf_address']).ip}:14550\n"
           "STUDENT_RELAY_UDP_OUT=udp:127.0.0.1:14552\n"
           "ENABLE_LOCAL_QGC_OUTPUT=true\nLOCAL_QGC_UDP_OUT=udp:127.0.0.1:14551\n"
           f"MAVPROXY_STREAMRATE=4\nWIFI_INTERFACE={cfg['hotspot_interface']}\n")
    dropins = UNITS / f"{PROXY}.d"
    return {
        ENV: (env, 0o640),
        STUDENT_CONFIG: (json.dumps(runtime, indent=2) + "\n", 0o640),
        OPT / "run-mavproxy.sh": (asset("run-mavproxy.sh"), 0o755),
        OPT / "student-relay.py": (asset("student-relay.py"), 0o755),
        SWITCH_CLI: (asset("gcs-student-control"), 0o755),
        UNITS / SWITCH: (asset(f"{SWITCH}.in").replace("__SERVICE_USER__", account), 0o644),
        UNITS / HOTSPOT_UNIT: (hotspot_service, 0o644),
        UNITS / PROXY: (asset(f"{PROXY}.in").replace("__SERVICE_USER__", account), 0o644),
        dropins / "30-student-relay.conf": (f"[Unit]\nWants={SWITCH}\nAfter={SWITCH}\n", 0o644),
    }


def verify_devices(cfg):
    if run("systemctl", "is-active", "--quiet", "NetworkManager", allow_failure=True).returncode:
        raise RuntimeError("NetworkManager must be running")
    for interface, kind in ((cfg["rf_interface"], "ethernet"), (cfg["hotspot_interface"], "wifi")):
        if output("nmcli", "-g", "GENERAL.TYPE", "device", "show", interface) != kind:
            raise RuntimeError(f"{interface} must be a NetworkManager {kind} device")


def verify_rf(cfg):
    interface = cfg["rf_interface"]
    rf = ipaddress.IPv4Interface(cfg["rf_address"])
    address = json.loads(output("ip", "-j", "-4", "address", "show", "dev", interface))[0]
    if not any(item.get("local") == str(rf.ip) and item.get("prefixlen") == rf.network.prefixlen
               and item.get("valid_life_time") == 4294967295 for item in address["addr_info"]):
        raise RuntimeError(f"Configure the static RF address {rf} on {interface} first")
    profile = output("nmcli", "-g", "GENERAL.CON-UUID", "device", "show", interface)
    if output("nmcli", "-g", "ipv4.method", "connection", "show", "uuid", profile) != "manual":
        raise RuntimeError("The RF NetworkManager profile must use manual IPv4")
    route = json.loads(output("ip", "-j", "-4", "route", "get", cfg["rf_peer"]))[0]
    if route.get("dev") != interface or route.get("prefsrc") != str(rf.ip):
        raise RuntimeError("The RF peer route has the wrong interface or source IP")


def prepare_rf(cfg):
    """Install a dedicated RF profile while keeping the previous profile available."""
    try:
        verify_rf(cfg)
        return
    except RuntimeError:
        pass
    interface = cfg["rf_interface"]
    link = json.loads(output("ip", "-j", "link", "show", "dev", interface))[0]
    if "LOWER_UP" not in link["flags"]:
        raise RuntimeError(f"Connect the Microhard Ethernet cable to {interface} first")
    defaults = json.loads(output("ip", "-j", "-4", "route", "show", "default"))
    if defaults and min(defaults, key=lambda route: route.get("metric", 0)).get("dev") == interface:
        raise RuntimeError("The RF interface carries the preferred default route. Configure it manually from the console.")
    ssh = os.environ.get("SSH_CONNECTION", "").split()
    if ssh:
        route = json.loads(output("ip", "-j", "route", "get", ssh[0]))[0]
        if route.get("dev") == interface:
            raise RuntimeError("Use the GCS console or a separate management interface")
    previous = output("nmcli", "-g", "GENERAL.CON-UUID", "device", "show", interface)
    name = "gcs-microhard"
    if name in output("nmcli", "-g", "NAME", "connection", "show").splitlines():
        raise RuntimeError(f"Review the existing {name} profile before configuring RF")
    profile = str(uuid.uuid4())
    run("nmcli", "connection", "add", "type", "ethernet", "ifname", interface,
        "con-name", name, "connection.uuid", profile, "connection.autoconnect", "no",
        "connection.autoconnect-priority", "100", "ipv4.method", "manual",
        "ipv4.addresses", cfg["rf_address"], "ipv4.never-default", "yes",
        "ipv4.dad-timeout", "3000", "ipv4.may-fail", "no", "ipv6.method", "disabled")
    try:
        run("nmcli", "--wait", "20", "connection", "up", "uuid", profile, "ifname", interface)
        verify_rf(cfg)
        run("nmcli", "connection", "modify", "uuid", profile, "connection.autoconnect", "yes")
    except (Exception, KeyboardInterrupt):
        run("nmcli", "connection", "delete", "uuid", profile)
        if previous and previous != "--":
            run("nmcli", "--wait", "20", "connection", "up", "uuid", previous, "ifname", interface)
        raise
    print(f"Static RF profile {name} uses {cfg['rf_address']} on {interface}.")


def preflight(cfg):
    verify_devices(cfg)
    verify_rf(cfg)


def nm_profile():
    prefix = f"{HOTSPOT}:"
    matches = [line[len(prefix):] for line in output(
        "nmcli", "-t", "-f", "NAME,UUID", "connection", "show"
    ).splitlines() if line.startswith(prefix)]
    if len(matches) > 1:
        raise RuntimeError("Multiple GCS Hotspot profiles found")
    return str(uuid.UUID(matches[0])) if matches else None


def hotspot_active(profile):
    return profile in output("nmcli", "-g", "UUID", "connection", "show", "--active").splitlines()


def keyfile_value(value):
    return value.replace("\\", "\\\\").replace(" ", "\\s")


def keyfile_path(profile):
    return NM_FILES / f"{HOTSPOT}-{profile}.nmconnection"


def new_hotspot(profile, cfg, password):
    settings = f"""[connection]
id={HOTSPOT}
uuid={profile}
type=wifi
interface-name={cfg['hotspot_interface']}
autoconnect=false

[wifi]
mode=ap
band=bg
powersave=2
ssid={keyfile_value(cfg['hotspot_ssid'])}

[wifi-security]
key-mgmt=wpa-psk
psk={keyfile_value(password)}

[ipv4]
method=shared
address1={cfg['hotspot_address']}
never-default=true

[ipv6]
method=disabled
"""
    path = keyfile_path(profile)
    write(path, settings, 0o600)
    run("nmcli", "connection", "load", path)


def manifest_write(record):
    write(MANIFEST, json.dumps(record, indent=2) + "\n", 0o600)


def manifest_read():
    if not MANIFEST.is_file():
        raise RuntimeError("Run the installation script before using start, stop or remove")
    record = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if record["version"] != 2 or record["origin"] not in ("fresh", "adopted"):
        raise RuntimeError("Unknown deployment record")
    record["profile"] = str(uuid.UUID(record["profile"]))
    return record


def start(record):
    reject_legacy()
    preflight(record["config"])
    profile = record["profile"]
    if nm_profile() != profile:
        raise RuntimeError("The managed Hotspot profile is missing or was replaced")
    sockets = output("ss", "-Hlunp", "sport = :14550")
    if any("mavproxy.py" not in line for line in sockets.splitlines()):
        raise RuntimeError("UDP 14550 is occupied. Set coach QGC to UDP 14551 first.")
    run("systemctl", "start", SWITCH, HOTSPOT_UNIT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = run(SWITCH_CLI, "status", allow_failure=True)
        if result.returncode == 0:
            status = result.stdout.strip()
            if json.loads(status)["mode"] in ("control", "monitor"):
                break
        time.sleep(0.2)
    else:
        raise RuntimeError("The student relay control socket did not become ready")
    if not hotspot_active(profile):
        run("nmcli", "--wait", "20", "connection", "up", "uuid", profile)
    run("systemctl", "start", PROXY)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if "mavproxy.py" in output("ss", "-Hlunp", "sport = :14550"):
            print(status)
            print("MAVProxy and Hotspot started. Check live telemetry in QGC.")
            return
        time.sleep(0.5)
    raise RuntimeError(f"MAVProxy did not open UDP 14550. Check journalctl -u {PROXY}")


def stop(record):
    units = [unit for unit in SERVICES if (UNITS / unit).is_file()]
    if units:
        run("systemctl", "stop", *units)
    profile = record["profile"]
    if hotspot_active(profile):
        run("nmcli", "--wait", "15", "connection", "down", "uuid", profile)


def reject_legacy():
    dropins = UNITS / f"{PROXY}.d"
    if any(path.exists() for path in (UNITS / "gcs-student-control.service",
            dropins / "30-student-control.conf", dropins / "40-rf-guard.conf",
            ETC / "student-control.json", ETC / "rf-guard.nft")):
        raise RuntimeError("Legacy firewall deployment found. Remove it with the old deployment package first.")


def remove(record):
    units = [unit for unit in SERVICES if (UNITS / unit).is_file()]
    if units:
        run("systemctl", "daemon-reload")
    stop(record)
    if units:
        run("systemctl", "disable", *units)
    profile = record["profile"]
    if nm_profile() == profile:
        run("nmcli", "connection", "delete", "uuid", profile)
    if record["origin"] == "fresh":
        keyfile_path(profile).unlink(missing_ok=True)
        if record["runtime_owned"] and VENV.is_dir() and not VENV.is_symlink():
            shutil.rmtree(VENV)
    for path in payload(record["config"], record["account"]):
        path.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    MANIFEST.unlink()
    print("Removed GCS forwarding services. RF addressing, QGC, and telemetry logs were retained.")


def adopt(cfg, files, profile, account):
    required_env = dict(line.split("=", 1) for line in files[ENV][0].splitlines())
    existing_env = dict(line.split("=", 1) for line in ENV.read_text().splitlines()
                        if "=" in line and not line.lstrip().startswith("#"))
    properties = output("nmcli", "-g",
                        "connection.interface-name,802-11-wireless.ssid,ipv4.method,ipv4.addresses",
                        "connection", "show", "uuid", profile).splitlines()
    if (any(existing_env.get(key) != value for key, value in required_env.items())
            or json.loads(STUDENT_CONFIG.read_text()) != student_config(cfg)
            or properties != [cfg["hotspot_interface"], cfg["hotspot_ssid"], "shared", cfg["hotspot_address"]]
            or f"User={account}" not in (UNITS / PROXY).read_text().splitlines()):
        raise RuntimeError("Existing GCS configuration differs from this bundle. Review config.json.")
    print("Existing RF services adopted without restarting them.")


def profile_files(profile):
    """Find persisted keyfiles by UUID, including NetworkManager runtime profiles."""
    found = []
    for directory in (NM_FILES, Path('/run/NetworkManager/system-connections'),
                      Path('/usr/lib/NetworkManager/system-connections')):
        for path in directory.glob('*.nmconnection'):
            settings = configparser.ConfigParser(interpolation=None)
            settings.read(path, encoding='utf-8')
            if settings.get('connection', 'uuid', fallback='') == profile:
                found.append(path)
    return found


def network_snapshot(profile, interface):
    files = profile_files(profile)
    if not files:
        raise RuntimeError(f'No saved NetworkManager keyfile for {interface}; configure it from the GCS console first')
    return {'profile': profile, 'interface': interface, 'files': [str(p) for p in files],
            'active': hotspot_active(profile)}


def check_network_access(interface):
    # Changing a dedicated data NIC is supported; keep the management path usable.
    defaults = json.loads(output('ip', '-j', '-4', 'route', 'show', 'default'))
    if defaults and min(defaults, key=lambda route: route.get('metric', 0)).get('dev') == interface:
        raise RuntimeError(f'{interface} carries the preferred default route; update it from the GCS console')
    ssh = os.environ.get('SSH_CONNECTION', '').split()
    if ssh:
        route = json.loads(output('ip', '-j', 'route', 'get', ssh[0]))[0]
        if route.get('dev') == interface:
            raise RuntimeError('Run this network update from the GCS console or a separate management interface')


def update(cfg, account, record):
    """Apply a changed root config to an installed GCS, restoring it on failure."""
    import pwd

    if account != record['account']:
        raise RuntimeError(f"Use the installed desktop account: --user {record['account']}")
    user = pwd.getpwnam(account)
    reject_legacy()
    verify_devices(cfg)
    if nm_profile() != record['profile']:
        raise RuntimeError('The managed Hotspot profile is missing or was replaced')
    old = record['config']
    files = payload(cfg, account)
    rf_changed = any(cfg[key] != old[key] for key in ('rf_interface', 'rf_address', 'rf_peer'))
    hotspot_changed = any(cfg[key] != old[key] for key in
                          ('hotspot_interface', 'hotspot_ssid', 'hotspot_address'))
    if not rf_changed:
        verify_rf(cfg)
    current = (cfg == old and all(path.is_file() and
               path.read_text(encoding='utf-8') == body for path, (body, _mode) in files.items()))
    if current:
        start(record)
        print('Installed MAVLink configuration is already current.')
        return
    snapshots = []
    if rf_changed:
        for interface in {old['rf_interface'], cfg['rf_interface']}:
            check_network_access(interface)
        rf_profile = str(uuid.UUID(output('nmcli', '-g', 'GENERAL.CON-UUID', 'device',
                                         'show', old['rf_interface'])))
        snapshots.append(network_snapshot(rf_profile, old['rf_interface']))
    if hotspot_changed:
        for interface in {old['hotspot_interface'], cfg['hotspot_interface']}:
            check_network_access(interface)
        snapshots.append(network_snapshot(record['profile'], old['hotspot_interface']))
    backup = Backup([*files, MANIFEST, *(Path(path) for snapshot in snapshots
                    for path in snapshot['files'])], 'mavlink', BACKUP_ROOT)
    backup.save_json('network.json', snapshots)
    states = {unit: run('systemctl', 'is-active', '--quiet', unit,
                       allow_failure=True).returncode == 0 for unit in SERVICES}
    backup.save_json('services.json', states)
    print(f'Configuration backup: {backup.directory}', flush=True)
    updated = {**record, 'config': cfg}
    try:
        if rf_changed:
            run('systemctl', 'stop', PROXY)
            args = ['nmcli', 'connection', 'modify', 'uuid', rf_profile,
                    'connection.interface-name', cfg['rf_interface'],
                    'ipv4.method', 'manual', 'ipv4.addresses', cfg['rf_address'],
                    'ipv4.routes', f"{cfg['rf_peer']}/32", 'ipv4.never-default', 'yes',
                    'connection.autoconnect', 'yes', 'ipv6.method', 'disabled']
            if cfg['rf_interface'] != old['rf_interface']:
                args += ['802-3-ethernet.mac-address', '']
            run(*args)
            run('nmcli', '--wait', '20', 'connection', 'up', 'uuid', rf_profile,
                'ifname', cfg['rf_interface'])
            verify_rf(cfg)
        if hotspot_changed:
            run('systemctl', 'stop', HOTSPOT_UNIT)
            if hotspot_active(record['profile']):
                run('nmcli', '--wait', '15', 'connection', 'down', 'uuid', record['profile'])
            run('nmcli', 'connection', 'modify', 'uuid', record['profile'],
                'connection.interface-name', cfg['hotspot_interface'],
                '802-11-wireless.ssid', cfg['hotspot_ssid'], 'ipv4.method', 'shared',
                'ipv4.addresses', cfg['hotspot_address'], 'ipv4.never-default', 'yes')
        for path, (body, mode) in files.items():
            write(path, body, mode)
        os.chown(ENV, 0, user.pw_gid)
        os.chown(STUDENT_CONFIG, 0, user.pw_gid)
        run('systemctl', 'daemon-reload')
        # A restart preserves /run/gcs-student-relay/mode.json and the coach's mode.
        run('systemctl', 'start', HOTSPOT_UNIT)
        run('systemctl', 'restart', SWITCH)
        run('systemctl', 'restart', PROXY)
        start(updated)
        manifest_write(updated)
        run('systemctl', 'enable', *SERVICES)
    except (Exception, KeyboardInterrupt):
        print('Update failed. Restoring the previous MAVLink configuration.', file=sys.stderr)
        try:
            run('systemctl', 'stop', PROXY)
            if hotspot_changed:
                run('systemctl', 'stop', HOTSPOT_UNIT)
            for snapshot in snapshots:
                if hotspot_active(snapshot['profile']):
                    run('nmcli', '--wait', '15', 'connection', 'down', 'uuid', snapshot['profile'])
                # nmcli may have moved a runtime keyfile to /etc when persisting changes.
                for path in profile_files(snapshot['profile']):
                    if str(path) not in snapshot['files']:
                        path.unlink()
            backup.restore()
            if snapshots:
                run('nmcli', 'connection', 'reload')
                for snapshot in snapshots:
                    if snapshot['active']:
                        run('nmcli', '--wait', '20', 'connection', 'up', 'uuid',
                            snapshot['profile'], 'ifname', snapshot['interface'])
            run('systemctl', 'daemon-reload')
            if states[SWITCH]:
                run('systemctl', 'restart', SWITCH)
            for unit, active in states.items():
                run('systemctl', 'start' if active else 'stop', unit)
        except Exception as error:
            print(f'Automatic rollback incomplete. Backup: {backup.directory}; {error}', file=sys.stderr)
        raise
    print('MAVLink configuration updated; current student control mode preserved.')


def install(cfg, account, configure_rf=False):
    import pwd

    user = pwd.getpwnam(account)
    if user.pw_uid == 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*", account):
        raise RuntimeError("Run sudo from the GCS login account, or supply --user")
    files = payload(cfg, account)
    reject_legacy()
    present = [path for path in files if path.exists() or path.is_symlink()]
    verify_devices(cfg)
    profile = nm_profile()
    if present or profile:
        if len(present) != len(files) or not profile or not (VENV / "bin/mavproxy.py").is_file():
            raise RuntimeError("Partial or incompatible GCS installation. Review before replacing it.")
        verify_rf(cfg)
        adopt(cfg, files, profile, account)
        manifest_write({"version": 2, "ready": True, "origin": "adopted", "config": cfg,
                        "account": account, "profile": profile})
        return
    if configure_rf:
        prepare_rf(cfg)
    else:
        verify_rf(cfg)
    if output("ss", "-Hlun", "sport = :14550"):
        raise RuntimeError("UDP 14550 is occupied. Set coach QGC to UDP 14551 before installing.")
    if STATE.exists():
        run("runuser", "-u", account, "--", "test", "-w", STATE)
    runtime_owned = not (VENV.exists() or VENV.is_symlink())
    if not runtime_owned:
        verify_runtime()
    if not sys.stdin.isatty():
        raise RuntimeError("Run in a GCS terminal so the Hotspot password can be entered securely")
    password = getpass("New GCS Hotspot password (8-63 ASCII characters): ")
    if not 8 <= len(password) <= 63 or not password.isascii() or not password.isprintable():
        raise ValueError("Hotspot password must be 8-63 printable ASCII characters")
    if password != getpass("Repeat Hotspot password: "):
        raise ValueError("Hotspot passwords differ")
    profile = str(uuid.uuid4())
    record = {"version": 2, "ready": False, "origin": "fresh", "config": cfg,
              "account": account, "profile": profile, "runtime_owned": runtime_owned}
    manifest_write(record)
    try:
        packages = ("python3-venv",)
        missing = [pkg for pkg in packages if run(
            "dpkg-query", "-W", "-f=${db:Status-Status}", pkg, allow_failure=True
        ).stdout.strip() != "installed"]
        if missing:
            print("Installing GCS system dependencies.", flush=True)
            run("apt-get", "update", timeout=600)
            run("apt-get", "-o", "DPkg::Lock::Timeout=300", "install",
                "-y", "--no-install-recommends", *missing, timeout=600)
        if runtime_owned:
            OPT.mkdir(parents=True, exist_ok=True)
            run(sys.executable, "-m", "venv", VENV)
            print("Installing MAVProxy into its virtual environment.", flush=True)
            run(VENV / "bin/python", "-m", "pip", "install", "MAVProxy==1.8.74", "future", timeout=600)
            verify_runtime()
        if not STATE.exists():
            STATE.mkdir(mode=0o750, parents=True)
            os.chown(STATE, user.pw_uid, user.pw_gid)
        for path, (content, mode) in files.items():
            write(path, content, mode)
        os.chown(ENV, 0, user.pw_gid)
        os.chown(STUDENT_CONFIG, 0, user.pw_gid)
        new_hotspot(profile, cfg, password)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", *SERVICES)
        start(record)
        record["ready"] = True
        manifest_write(record)
    except (Exception, KeyboardInterrupt):
        print("Installation failed. Attempting rollback.", file=sys.stderr)
        try:
            remove(record)
        except Exception as cleanup:
            print(f"Rollback incomplete: {cleanup}. Run the removal script to retry.", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "start", "stop", "remove", "check"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--user", default=os.environ.get("SUDO_USER"))
    parser.add_argument("--configure-rf", action="store_true",
                        help="create a static profile on the dedicated RF adapter during fresh installation")
    args = parser.parse_args()
    if args.configure_rf and args.action != "install":
        parser.error("--configure-rf applies only to installation")
    if args.action == "check":
        cfg = load_config(args.config)
        files = payload(cfg, args.user or "user")
        ast.parse(files[SWITCH_CLI][0])
        ast.parse(files[OPT / "student-relay.py"][0])
        print(f"Validated config.json and {len(files)} deployment files")
        return
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Use sudo bash on the Ubuntu GCS")
    import fcntl

    with LOCK_FILE.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "install" and not MANIFEST.exists():
            if not args.user:
                parser.error("Run sudo from the GCS login account or pass --user")
            install(load_config(args.config), args.user, args.configure_rf)
            return
        record = manifest_read()
        if args.action == "remove":
            remove(record)
        elif not record["ready"]:
            raise RuntimeError("Previous installation incomplete. Run the removal script, then install again.")
        elif args.action == "stop":
            stop(record)
            print("MAVProxy and Hotspot stopped. Boot startup remains enabled.")
        elif args.action == "start":
            start(record)
        else:
            update(load_config(args.config), args.user or record['account'], record)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f"ERROR: {error}")
