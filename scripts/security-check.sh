#!/bin/sh
# Everything a reviewer of a public repository holding a personal Telegram
# session would want to see run: lint, Python security lint, a dependency
# vulnerability audit, shell checking, and a sweep for committed secrets.
#
# Run it locally with ./scripts/security-check.sh; CI runs this same file.
set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT" || exit 1

FAILED=""
note() { printf '\n=== %s\n' "$1"; }
fail() { FAILED="$FAILED $1"; }

PYTHON=./.venv/bin/python
[ -x "$PYTHON" ] || PYTHON=python3

note "ruff — lint"
"$PYTHON" -m ruff check . || fail ruff

note "bandit — Python security lint"
"$PYTHON" -m bandit -q -c pyproject.toml -r src || fail bandit

note "pip-audit — known vulnerabilities in the installed dependencies"
"$PYTHON" -m pip_audit --progress-spinner off || fail pip-audit

note "shellcheck — the shell entry points"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck bin/telegram-mcp bin/telegram-login scripts/*.sh || fail shellcheck
elif [ -n "${CI:-}" ]; then
    echo "shellcheck is missing in CI, where it must always run" >&2
    fail shellcheck-missing
else
    echo "shellcheck is not installed, skipping it: brew install shellcheck," >&2
    echo "or apt-get install shellcheck. CI runs it on every push regardless." >&2
fi

# Shapes that cannot be innocent: an API hash, a phone number, a bot token, a
# generic secret key. Tracked files only, so .venv and downloads stay out.
SHAPES='[0-9a-f]{32}|\+?\b[78][0-9]{10}\b|[0-9]{8,10}:[A-Za-z0-9_-]{35}|sk-[A-Za-z0-9]{20,}'

note "secret sweep — tracked files"
if git grep -InE "$SHAPES" -- .; then
    echo "the lines above look like credentials" >&2
    fail secrets-in-tree
fi

note "secret sweep — nothing secret is tracked at all"
if git ls-files | grep -E '(^|/)\.env$|\.session(-journal)?$'; then
    echo "the files above must never be tracked" >&2
    fail secret-files-tracked
fi

# History needs its own patterns. A bare hex string is too common in fixtures to
# ban outright, but an *assignment* of one is not — and an added-then-removed
# commit is exactly the case the working-tree sweep above cannot see.
HISTORY_SHAPES='\+?\b[78][0-9]{10}\b|[0-9]{8,10}:[A-Za-z0-9_-]{35}|sk-[A-Za-z0-9]{20,}'
HISTORY_SHAPES="$HISTORY_SHAPES"'|(api[_-]?hash|api[_-]?id|token|secret)[[:space:]]*[=:][[:space:]]*.?[0-9a-f]{32}'

note "secret sweep — history"
if git log -p --all | grep -qiE "$HISTORY_SHAPES"; then
    echo "history contains an assigned credential, or something phone- or token-shaped" >&2
    fail secrets-in-history
fi

# Optional and local-only: if this machine has the real credentials exported,
# prove the history never contained them. The value is never printed.
if [ -n "${TELEGRAM_API_HASH:-}" ]; then
    note "secret sweep — history against the live API hash"
    if git log -p --all | grep -qF "$TELEGRAM_API_HASH"; then
        echo "history contains the live API hash" >&2
        fail live-hash-in-history
    fi
fi

note "personal data sweep — no absolute home paths"
if git grep -InE '/(Users|home)/[a-z]' -- .; then
    echo "the lines above carry someone's home directory" >&2
    fail personal-paths
fi

printf '\n'
if [ -n "$FAILED" ]; then
    echo "FAILED:$FAILED"
    exit 1
fi
echo "All security checks passed."
