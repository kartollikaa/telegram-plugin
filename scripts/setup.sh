#!/bin/sh
# Prewarms the private runtime without opening Telegram.
set -e
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/bin/telegram" --help >/dev/null
echo "Dependencies are ready."
echo "Next: use /telegram:login, or run $ROOT/bin/telegram-login --browser."
