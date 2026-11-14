#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
html="$repo_root/docs/preview/preview.html"
output="$repo_root/preview.png"

if [[ -n "${CHROME_BIN:-}" ]]; then
  browser="$CHROME_BIN"
elif command -v google-chrome >/dev/null 2>&1; then
  browser="$(command -v google-chrome)"
elif command -v google-chrome-stable >/dev/null 2>&1; then
  browser="$(command -v google-chrome-stable)"
elif command -v chromium >/dev/null 2>&1; then
  browser="$(command -v chromium)"
elif command -v chromium-browser >/dev/null 2>&1; then
  browser="$(command -v chromium-browser)"
else
  echo "No supported headless Chrome binary found." >&2
  exit 1
fi

"$browser" --headless=new --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1600,900 --screenshot="$output" "file://$html"
