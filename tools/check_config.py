#!/usr/bin/env python3
"""Validate portable bundle configuration without touching host networking."""
import argparse
import sys

sys.dont_write_bytecode = True

from configuration import DEFAULT_CONFIG, load_config, video_url

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', default=DEFAULT_CONFIG)
parser.add_argument('--video-url', action='store_true')
args = parser.parse_args()
try:
    cfg = load_config(args.config)
except (KeyError, TypeError, ValueError, OSError) as error:
    sys.exit(f'Invalid configuration: {error}')
print(video_url(cfg) if args.video_url else
      f'Configuration valid. QGC video URL: {video_url(cfg)}')
