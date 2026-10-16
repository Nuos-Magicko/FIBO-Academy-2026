#!/bin/sh
# Flash and verify every connected XIAO ESP32S3 (Linux/macOS). Extra arguments pass through, e.g.
#   pc/tools/flash_all.sh --image /path/gmt_people_flow_merged.bin
pc=$(cd "$(dirname "$0")/.." && pwd)
exec "$pc/.venv/bin/python" "$pc/main.py" verify-board --all --flash "$@"
