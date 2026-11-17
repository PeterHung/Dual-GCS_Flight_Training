#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$(readlink -f -- "$0")")/tools/common.sh"
account="${SUDO_USER:-user}"
config_file="$PACKAGE_ROOT/config.json"
mavlink_args=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --user)
            [[ "$#" -ge 2 ]] || { echo '--user requires an account name.' >&2; exit 2; }
            account="$2"; shift 2 ;;
        --configure-rf)
            mavlink_args+=("$1"); shift ;;
        --config)
            [[ "$#" -ge 2 ]] || { echo '--config requires a JSON file.' >&2; exit 2; }
            config_file="$2"; shift 2 ;;
        --help|-h)
            echo 'Usage: sudo bash ./01_install.sh [--user user] [--configure-rf] [--config path]'
            echo 'Install or update using root config.json. Camera networking is prepared separately. See SOP.md.'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
require_root
config_file="$(readlink -f -- "$config_file")"
exec 9>/run/lock/gcs-complete-install.lock
flock -n 9 || { echo 'Another complete install/update is running.' >&2; exit 1; }
python3 "$PACKAGE_ROOT/tools/check_config.py" --config "$config_file"
bash "$PACKAGE_ROOT/mavlink/01_install.sh" --config "$config_file" --user "$account" "${mavlink_args[@]}"
if ! bash "$PACKAGE_ROOT/video/01_install.sh" --config "$config_file" --user "$account"; then
    echo 'Video installation failed. MAVLink may already be installed and running.' >&2
    echo 'Review the physical camera URL and rerun video/01_install.sh. See SOP.md.' >&2
    exit 1
fi
echo 'Services installed/updated. Set BOTH QGC RTSP URLs to:'
python3 "$PACKAGE_ROOT/tools/check_config.py" --config "$config_file" --video-url
