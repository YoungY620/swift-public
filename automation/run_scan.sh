#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${FORECAST_EDGE_ENV_FILE:-"$HOME/.config/forecast-edge/forecast-edge.env"}

if [ ! -f "$ENV_FILE" ]; then
  echo "forecast-edge environment file not found: $ENV_FILE" >&2
  exit 2
fi

set -a
# This file is user-controlled configuration stored outside the repository.
. "$ENV_FILE"
set +a

exec python3 "$SCRIPT_DIR/run_scheduled_scan.py" --repository-root "$REPO_ROOT"
