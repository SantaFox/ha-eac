#!/usr/bin/env bash
# Push custom_components/eac_cyprus/ to a Home Assistant Samba share.
# Reads HA_HOST / HA_SMB_USER / HA_SMB_PASS from .env (gitignored).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

: "${HA_HOST:?set HA_HOST in .env (e.g. 192.168.1.32)}"
: "${HA_SMB_USER:?set HA_SMB_USER in .env (e.g. homeassistant)}"
: "${HA_SMB_PASS:?set HA_SMB_PASS in .env}"
HA_SMB_WORKGROUP="${HA_SMB_WORKGROUP:-WORKGROUP}"
HA_SMB_SHARE="${HA_SMB_SHARE:-config}"

cd "$ROOT/custom_components/eac_cyprus"

SCRIPT=$(mktemp)
trap 'rm -f "$SCRIPT"' EXIT

{
  echo "cd custom_components"
  echo "mkdir eac_cyprus" 2>/dev/null || true
  echo "cd eac_cyprus"
  echo "lcd ."
  for f in *.py *.json; do echo "put $f"; done
  echo "mkdir translations" 2>/dev/null || true
  echo "cd translations"
  echo "lcd translations"
  for f in translations/*.json; do echo "put $(basename "$f")"; done
} > "$SCRIPT"

smbclient "//$HA_HOST/$HA_SMB_SHARE" -U "$HA_SMB_USER%$HA_SMB_PASS" -W "$HA_SMB_WORKGROUP" < "$SCRIPT" \
  | grep -E '^putting|^NT_STATUS' || true

echo "deployed to //$HA_HOST/$HA_SMB_SHARE/custom_components/eac_cyprus/"
echo "now restart HA: Settings → System → Restart  (or 'ha core restart' via SSH)"
