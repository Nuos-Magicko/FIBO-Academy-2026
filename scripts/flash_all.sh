#!/bin/sh
# Flash and verify every connected XIAO ESP32S3 (Linux/macOS). Extra arguments pass through, e.g.
#   ./scripts/flash_all.sh --image /path/gmt_people_flow_merged.bin
root=$(cd "$(dirname "$0")/.." && pwd)
exec "$root/pc/.venv/bin/python" "$root/scripts/verify_board.py" --all --flash "$@"
