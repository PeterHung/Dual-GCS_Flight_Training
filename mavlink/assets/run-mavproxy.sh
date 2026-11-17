#!/usr/bin/env bash
# Runtime wrapper installed by deploy_gcs.sh. It is deliberately kept in the
# deployment bundle so the running command is reviewable before deployment.

set -Eeuo pipefail

readonly CONFIG_FILE="/etc/gcs/gcs-mavproxy.env"
readonly MAVPROXY_BIN="/opt/gcs/venv/bin/mavproxy.py"
readonly STATE_DIR="/var/lib/gcs/mavproxy"

# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${MAVPROXY_MASTER:?MAVPROXY_MASTER is required}"
: "${STUDENT_RELAY_UDP_OUT:?STUDENT_RELAY_UDP_OUT is required}"

args=(
  --master="$MAVPROXY_MASTER"
  --out="$STUDENT_RELAY_UDP_OUT"
  --state-basedir="$STATE_DIR"
  --streamrate="${MAVPROXY_STREAMRATE:-4}"
  --cmd="set mavfwd true"
  --non-interactive
)

if [[ "${ENABLE_LOCAL_QGC_OUTPUT:-true}" == "true" ]]; then
  : "${LOCAL_QGC_UDP_OUT:?LOCAL_QGC_UDP_OUT is required when local QGC output is enabled}"
  args+=(--out="$LOCAL_QGC_UDP_OUT")
fi

exec "$MAVPROXY_BIN" "${args[@]}"
