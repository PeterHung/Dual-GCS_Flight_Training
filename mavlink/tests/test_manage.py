#!/usr/bin/env python3
"""Verify deployment lifecycle effects without changing the host configuration."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


BUNDLE = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def exercise(self, scenario):
        spec = importlib.util.spec_from_file_location("deployment", BUNDLE / "assets/manage.py")
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        cfg = manager.load_config(BUNDLE.parent / "config.json")
        with tempfile.TemporaryDirectory(prefix="gcs-manage-") as directory:
            root = Path(directory)
            manager.ETC, manager.OPT, manager.UNITS, manager.NM_FILES = (
                root / name for name in ("etc", "opt", "systemd", "nm"))
            manager.STATE = root / "state"
            manager.MANIFEST = manager.ETC / "rf-bundle.json"
            manager.VENV = manager.OPT / "venv"
            manager.SWITCH_CLI = root / "student-control"
            manager.ENV = manager.ETC / "gcs-mavproxy.env"
            manager.STUDENT_CONFIG = manager.ETC / "student-relay.json"
            state = dict(profile=None, hotspot=False, proxy=False)
            calls = []

            def result(stdout=""):
                return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

            def run(*args, **kwargs):
                args = tuple(str(arg) for arg in args)
                calls.append(args)
                tool = args[0]
                self.assertNotIn(Path(tool).name, ("nft", "iptables", "ip6tables"))
                if tool == "ip":
                    if "address" in args:
                        return result(json.dumps([{"addr_info": [{"local": "192.168.168.249",
                            "prefixlen": 24, "valid_life_time": 4294967295}]}]))
                    return result(json.dumps([{"dev": cfg["rf_interface"], "prefsrc": "192.168.168.249"}]))
                if tool == "ss":
                    return result("mavproxy.py" if state["proxy"] else "")
                if tool == "nmcli":
                    if "GENERAL.TYPE" in args:
                        return result("ethernet" if args[-1] == cfg["rf_interface"] else "wifi")
                    if "GENERAL.CON-UUID" in args:
                        return result(str(uuid.UUID(int=1)))
                    if "ipv4.method" in args:
                        return result("manual")
                    if "NAME,UUID" in args:
                        return result(f'{manager.HOTSPOT}:{state["profile"]}' if state["profile"] else "")
                    if "--active" in args:
                        return result(state["profile"] if state["hotspot"] else "")
                    if "connection.interface-name,802-11-wireless.ssid,ipv4.method,ipv4.addresses" in args:
                        return result("\n".join((cfg["hotspot_interface"], cfg["hotspot_ssid"],
                                                  "shared", cfg["hotspot_address"])))
                    if "load" in args:
                        state["profile"] = Path(args[-1]).stem.removeprefix(manager.HOTSPOT + "-")
                    elif "delete" in args:
                        state["profile"] = None
                    elif "down" in args:
                        state["hotspot"] = False
                    elif "up" in args:
                        state["hotspot"] = True
                    return result()
                if tool == "systemctl":
                    if args[1] == "start":
                        if manager.HOTSPOT_UNIT in args:
                            state["hotspot"] = True
                        if manager.PROXY in args:
                            if scenario == "failure":
                                raise RuntimeError("simulated proxy failure")
                            state["proxy"] = True
                    if args[1] == "stop":
                        state["proxy"] = False
                    return result()
                if tool == str(manager.SWITCH_CLI):
                    return result('{"mode":"control"}')
                if tool == "dpkg-query":
                    return result("installed")
                if tool == sys.executable and "venv" in args:
                    binary = manager.VENV / "bin/mavproxy.py"
                    binary.parent.mkdir(parents=True)
                    binary.touch()
                    return result()
                if tool in (str(manager.VENV / "bin/mavproxy.py"), str(manager.VENV / "bin/python")):
                    return result()
                raise AssertionError(args)

            with patch.object(manager, "run", run), patch.object(manager, "getpass", lambda _: "example-pass"), \
                 patch.object(sys.stdin, "isatty", lambda: True), patch.object(os, "chown", create=True), \
                 patch.dict(sys.modules, {"pwd": SimpleNamespace(
                     getpwnam=lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000))}):
                if scenario == "adopt":
                    state.update(profile=str(uuid.uuid4()), hotspot=True, proxy=True)
                    for path, (body, mode) in manager.payload(cfg, "user").items():
                        manager.write(path, body, mode)
                    binary = manager.VENV / "bin/mavproxy.py"
                    binary.parent.mkdir(parents=True)
                    binary.touch()
                try:
                    manager.install(cfg, "user")
                except RuntimeError as error:
                    self.assertEqual(scenario, "failure")
                    self.assertEqual(str(error), "simulated proxy failure")
                if scenario != "failure":
                    record = manager.manifest_read()
                    self.assertTrue(record["ready"])
                    manager.stop(record)
                    self.assertFalse(state["hotspot"])
                    self.assertFalse(state["proxy"])
                    manager.start(record)
                    self.assertTrue(state["proxy"])
                    manager.remove(record)
                self.assertFalse(manager.MANIFEST.exists())
                self.assertIsNone(state["profile"])
                self.assertFalse(state["proxy"])
                self.assertTrue(all(not path.exists() for path in manager.payload(cfg, "user")))
                self.assertEqual(manager.VENV.exists(), scenario == "adopt")
                return calls

    def test_fresh_start_stop_remove(self):
        self.exercise("fresh")

    def test_failure_rolls_back(self):
        self.exercise("failure")

    def test_adopted_runtime_is_retained(self):
        self.exercise("adopt")


if __name__ == "__main__":
    unittest.main()
