#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/tools/common.sh"
no_arguments "$@"
require_root
video_state="$(systemctl show gcs-video-relay.service -p LoadState --value 2>/dev/null || true)"
if [[ -n "$video_state" && "$video_state" != 'not-found' ]]; then
    bash "$PACKAGE_ROOT/video/03_stop.sh"
fi
if [[ -f /etc/gcs/rf-bundle.json ]]; then
    bash "$PACKAGE_ROOT/mavlink/03_stop.sh"
else
    echo 'No MAVLink manifest found; MAVLink stop skipped.'
fi
echo 'Managed services stopped. Boot startup remains enabled.'
