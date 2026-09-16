#!/bin/sh
# Thin wrapper. Prefer: sudo python3 /path/to/copy-server/nas.py
ROOT=/mnt/zeus/Dump/copy-server
if [ ! -f "$ROOT/nas.py" ]; then
  ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
fi
exec python3 "$ROOT/nas.py" "$@"
