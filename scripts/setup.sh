#!/bin/sh
# Prewarms the private runtime without opening Telegram.
set -e
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
STATE=${TELEGRAM_STATE_DIR:-"$HOME/.local/state/telegram-plugin"}
"$ROOT/bin/telegram" --help >/dev/null
echo "Dependencies are ready."
echo "Next: put TELEGRAM_API_ID and TELEGRAM_API_HASH into $STATE/.env"
echo "      (copy $ROOT/.env.example as a starting point), then either run"
echo "      /telegram:login in a session, or $ROOT/bin/telegram-login here."
