#!/bin/sh
# Optional: does up front exactly what bin/telegram-mcp would do on its first run.
set -e
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
printf '' | "$ROOT/bin/telegram-mcp" >/dev/null
echo "Dependencies are ready. Next: $ROOT/bin/telegram-login"
