import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from configuration import load_config, video_url


class ConfigurationTests(unittest.TestCase):
    def test_windows_bom_and_alternate_hotspot(self):
        cfg = load_config(ROOT / 'config.json')
        cfg.update(hotspot_address='10.43.0.1/24', student_hotspot_ip='10.43.0.210')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text(json.dumps(cfg), encoding='utf-8-sig')
            self.assertEqual(video_url(load_config(path)), 'rtsp://10.43.0.1:8554/camera')

    def test_invalid_student_subnet_and_video_port_are_rejected(self):
        for changes in ({'student_hotspot_ip': '10.99.0.2'}, {'video': {'port': 80}}):
            cfg = load_config(ROOT / 'config.json')
            cfg.update(changes)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'config.json'
                path.write_text(json.dumps(cfg))
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == '__main__':
    unittest.main()
