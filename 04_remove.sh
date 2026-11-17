#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/tools/common.sh"
no_arguments "$@"
require_root
# Remove video before removing its Hotspot dependency.
bash "$PACKAGE_ROOT/video/04_remove.sh"
if [[ -f /etc/gcs/rf-bundle.json ]]; then
    bash "$PACKAGE_ROOT/mavlink/04_remove.sh"
else
    for unit in gcs-mavproxy.service gcs-student-relay.service gcs-hotspot.service; do
        if [[ -f "/etc/systemd/system/$unit" ]]; then
            echo "MAVLink manifest missing but $unit remains. Review manually; files were retained." >&2
            exit 1
        fi
    done
    echo 'No MAVLink installation found.'
fi
echo 'Removed managed forwarding services. RF/camera network profiles, QGC and logs retained.'
