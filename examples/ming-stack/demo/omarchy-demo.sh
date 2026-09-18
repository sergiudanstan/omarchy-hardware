#!/usr/bin/env bash
# Run the greenhouse demo on the Omarchy desktop:
#
#   - opens the Grafana "Greenhouse" dashboard as a web app window,
#   - starts desktop notifications (demo/notify.py) for the fan and the heat.
#
# Then ask Claude about the greenhouse in Claude Code (see demo/README.md).
#
#   ./demo/omarchy-demo.sh [--trust-ca] [--url https://pi.local:3000]
#
# --trust-ca adds this stack's CA to the browser's certificate store
# (~/.pki/nssdb, used by Chromium and Chrome) so the dashboard opens without a
# warning. The CA is name-constrained: it can only vouch for the stack's own
# names and addresses, never for other sites. Remove it again with:
#   certutil -d sql:$HOME/.pki/nssdb -D -n "MING example CA"
set -euo pipefail
cd "$(dirname "$0")/.."

URL=https://127.0.0.1:3000
TRUST=false
while (($#)); do
  case $1 in
    --trust-ca) TRUST=true ;;
    --url) URL=$2; shift ;;
    *) echo "usage: $0 [--trust-ca] [--url https://host:3000]" >&2; exit 2 ;;
  esac
  shift
done

PY=${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-hardware/venv/bin/python
CA=${MING_CA:-$HOME/.config/omarchy-hardware/ming/ming-ca.pem}
[[ -x $PY ]] || { echo "omarchy-hardware is not set up: run bin/setup.sh in the plugin first" >&2; exit 1; }
[[ -r $CA ]] || { echo "no CA at $CA: run ../bootstrap.sh with MING_CLIENT_DIR, or copy client/ there" >&2; exit 1; }

if $TRUST; then
  command -v certutil >/dev/null || { echo "certutil missing: install the nss package" >&2; exit 1; }
  if ! openssl x509 -in "$CA" -noout -ext nameConstraints 2>/dev/null | grep -q Permitted; then
    echo "refusing to trust $CA: it has no name constraints, so it could vouch for any site." >&2
    echo "Re-issue it with the current bootstrap.sh (delete certs/ first)." >&2
    exit 1
  fi
  mkdir -p "$HOME/.pki/nssdb"
  certutil -d "sql:$HOME/.pki/nssdb" -D -n "MING example CA" 2>/dev/null || true
  certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n "MING example CA" -i "$CA"
  echo "Trusted the MING example CA in ~/.pki/nssdb (restart the browser if it was open)."
fi

if pgrep -f "demo/notify.py" >/dev/null; then
  echo "Notifications already running."
else
  PYTHONPATH="$(cd ../.. && pwd)/mcp" setsid "$PY" -u demo/notify.py >/dev/null 2>&1 &
  echo "Notifications started (stop with: pkill -f demo/notify.py)."
fi

dashboard="$URL/d/greenhouse?kiosk&refresh=5s"
if command -v omarchy-launch-webapp >/dev/null; then
  omarchy-launch-webapp "$dashboard" >/dev/null 2>&1 &
else
  xdg-open "$dashboard" >/dev/null 2>&1 &
fi
echo "Opened $URL/d/greenhouse (log in as admin; password in secrets/grafana-admin-password)."
