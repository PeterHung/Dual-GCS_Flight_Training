#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/tools/common.sh"
no_arguments "$@"
require_root
systemctl show gcs-hotspot.service gcs-mavproxy.service \
    gcs-student-relay.service gcs-video-relay.service \
    -p Id -p LoadState -p ActiveState -p SubState -p UnitFileState -p MainPID -p NRestarts
if [[ -x /usr/local/sbin/gcs-student-control ]]; then
    /usr/local/sbin/gcs-student-control status || echo 'Student control unavailable; check relay service.'
fi
nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
video_port="$(python3 -c 'import json; from pathlib import Path; p=Path("/etc/gcs/video-relay.json"); print(json.loads(p.read_text())["port"] if p.exists() else 8554)')"
ss -ltnp "sport = :$video_port"
ss -lunp 'sport = :14550'
