#!/usr/bin/env bash
set -euo pipefail
PACKAGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        echo 'Run this script with sudo on the Ubuntu GCS.' >&2
        exit 1
    fi
}

no_arguments() {
    if [[ "$#" -ne 0 ]]; then
        echo 'This script does not take arguments. See SOP.md.' >&2
        exit 2
    fi
}
