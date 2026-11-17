#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/tools/common.sh"
no_arguments "$@"
require_root
bash "$PACKAGE_ROOT/mavlink/02_start.sh"
bash "$PACKAGE_ROOT/video/02_start.sh"
echo 'MAVLink, Hotspot, student relay and video relay started.'
