#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

URL="http://127.0.0.1:8000"
LOG_DIR="$APP_DIR/storage/logs"
mkdir -p "$LOG_DIR"

show_error() {
  local message="$1"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="DXA Quality" --text="$message" || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send "DXA Quality" "$message" || true
  else
    printf '%s\n' "$message" >&2
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  show_error "Docker is not installed or is not available in PATH."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  show_error "Docker daemon is not running. Start Docker, then open DXA Quality again."
  exit 1
fi

if ! docker compose up -d --build >"$LOG_DIR/launcher.log" 2>&1; then
  show_error "Container startup failed. See storage/logs/launcher.log."
  exit 1
fi

for _ in $(seq 1 60); do
  if command -v curl >/dev/null 2>&1 && curl -fsS "$URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v google-chrome >/dev/null 2>&1; then
  exec google-chrome --app="$URL"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium --app="$URL"
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser --app="$URL"
elif command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$URL"
else
  show_error "Application is running at $URL, but no graphical browser opener was found."
  exit 1
fi
