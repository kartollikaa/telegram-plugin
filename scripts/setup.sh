#!/bin/sh
# Optional: does up front exactly what bin/telegram-mcp would do on its first run.
set -e
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
STATE=${TELEGRAM_STATE_DIR:-"$HOME/.local/state/telegram-plugin"}
printf '' | "$ROOT/bin/telegram-mcp" >/dev/null
echo "Dependencies are ready."
echo "Next: put TELEGRAM_API_ID and TELEGRAM_API_HASH into $STATE/.env"
echo "      (copy $ROOT/.env.example as a starting point), then run:"
echo "      $ROOT/bin/telegram-login"
