#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_kimi_code_with_idle_timeout.sh \
  --idle-seconds SECONDS \
  --poll-seconds SECONDS \
  --stdout-log PATH \
  --stderr-log PATH \
  -- COMMAND [ARG ...]
EOF
}

if [[ "${1:-}" == "--child" ]]; then
  shift
  pid_file=$1
  result_file=$2
  stdout_log=$3
  stderr_log=$4
  shift 4
  if [[ "${1:-}" != "--" ]]; then
    usage
    exit 2
  fi
  shift
  printf '%s\n' "$$" > "$pid_file"
  set +e
  set -o pipefail
  stdbuf -oL -eL "$@" \
    2> >(stdbuf -oL tee "$stderr_log" >&2) \
    | stdbuf -oL tee "$stdout_log"
  command_status=${PIPESTATUS[0]}
  set -e
  printf '%s\n' "$command_status" > "$result_file"
  exit "$command_status"
fi

idle_seconds=
poll_seconds=10
stdout_log=
stderr_log=
while (( $# > 0 )); do
  case "$1" in
    --idle-seconds)
      idle_seconds=${2:-}
      shift 2
      ;;
    --poll-seconds)
      poll_seconds=${2:-}
      shift 2
      ;;
    --stdout-log)
      stdout_log=${2:-}
      shift 2
      ;;
    --stderr-log)
      stderr_log=${2:-}
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ ! "$idle_seconds" =~ ^[1-9][0-9]*$ ]] || \
   [[ ! "$poll_seconds" =~ ^[1-9][0-9]*$ ]] || \
   [[ -z "$stdout_log" || -z "$stderr_log" ]] || \
   (( $# == 0 )); then
  usage
  exit 2
fi

umask 077
mkdir -p "$(dirname "$stdout_log")" "$(dirname "$stderr_log")"
: > "$stdout_log"
: > "$stderr_log"

watchdog_dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/kimi-idle-watchdog.XXXXXX")
pid_file="$watchdog_dir/session.pid"
result_file="$watchdog_dir/result"
session_pid=

cleanup() {
  if [[ -n "$session_pid" ]] && kill -0 "$session_pid" 2>/dev/null; then
    kill -TERM -- "-$session_pid" 2>/dev/null || true
  fi
  if [[ -d "$watchdog_dir" ]]; then
    find "$watchdog_dir" -depth -delete
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

setsid --fork "$0" --child \
  "$pid_file" "$result_file" "$stdout_log" "$stderr_log" -- "$@" &
launcher_pid=$!
set +e
wait "$launcher_pid"
launcher_status=$?
set -e
if (( launcher_status != 0 )); then
  echo "Kimi Code watchdog could not launch the child process group" >&2
  exit "$launcher_status"
fi

for _ in $(seq 1 100); do
  if [[ -s "$pid_file" ]]; then
    session_pid=$(<"$pid_file")
    break
  fi
  sleep 0.1
done
if [[ ! "$session_pid" =~ ^[1-9][0-9]*$ ]]; then
  echo "Kimi Code watchdog could not establish the child process group" >&2
  exit 1
fi

total_bytes() {
  local stdout_bytes stderr_bytes
  stdout_bytes=$(stat -c '%s' "$stdout_log")
  stderr_bytes=$(stat -c '%s' "$stderr_log")
  printf '%s\n' "$((stdout_bytes + stderr_bytes))"
}

last_bytes=$(total_bytes)
last_activity_epoch=$(date +%s)
idle_timeout=false
while kill -0 "$session_pid" 2>/dev/null; do
  if [[ -s "$result_file" ]]; then
    break
  fi
  sleep "$poll_seconds"
  current_bytes=$(total_bytes)
  current_epoch=$(date +%s)
  if (( current_bytes != last_bytes )); then
    last_bytes=$current_bytes
    last_activity_epoch=$current_epoch
    continue
  fi
  if (( current_epoch - last_activity_epoch < idle_seconds )); then
    continue
  fi

  idle_timeout=true
  printf '%s\n' \
    "Kimi Code emitted no stdout or stderr for ${idle_seconds}s; terminating this attempt" \
    | tee -a "$stderr_log" >&2
  kill -TERM -- "-$session_pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$session_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$session_pid" 2>/dev/null; then
    kill -KILL -- "-$session_pid" 2>/dev/null || true
  fi
  break
done

if [[ "$idle_timeout" == "true" ]]; then
  exit 124
fi
if [[ -s "$result_file" ]] && [[ "$(<"$result_file")" =~ ^[0-9]+$ ]]; then
  exit "$(<"$result_file")"
fi
echo "Kimi Code child process exited without recording a status" >&2
exit 1
