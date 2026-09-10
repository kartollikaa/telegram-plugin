#!/bin/sh
# Optional. Installs the dependencies up front, exactly as the first run of
# bin/telegram-mcp would, then points at the login. Inside a session, prefer the
# /telegram:login command, which does all of this and the login itself.
set -e
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
STATE=${TELEGRAM_STATE_DIR:-"$HOME/.local/state/telegram-plugin"}
printf '' | "$ROOT/bin/telegram-mcp" >/dev/null
echo "Dependencies are ready."
echo "Next: put TELEGRAM_API_ID and TELEGRAM_API_HASH into $STATE/.env"
echo "      (copy $ROOT/.env.example as a starting point), then either run"
echo "      /telegram:login in a session, or $ROOT/bin/telegram-login here."
