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
else
    echo "shellcheck is not installed; install it locally (CI always runs it)" >&2
    fail shellcheck-missing
fi

# Shapes that cannot be innocent: an API hash, a phone number, a bot token, a
# generic secret key. Tracked files only, so .venv and downloads stay out.
SHAPES='[0-9a-f]{32}|\+?\b[78][0-9]{10}\b|[0-9]{8,10}:[A-Za-z0-9_-]{35}|sk-[A-Za-z0-9]{20,}'

note "secret sweep — tracked files"
if git ls-files -z | xargs -0 grep -InE "$SHAPES"; then
    echo "the lines above look like credentials" >&2
    fail secrets-in-tree
fi

note "secret sweep — nothing secret is tracked at all"
if git ls-files | grep -E '(^|/)\.env$|\.session(-journal)?$'; then
    echo "the files above must never be tracked" >&2
    fail secret-files-tracked
fi

# History is swept for shapes that stay recognisable in a diff. A bare hex string
# is not among them: low-entropy hex appears legitimately in test fixtures, and a
# real hash would also have to pass the working-tree sweep above.
note "secret sweep — history"
if git log -p --all | grep -qE '\+?\b[78][0-9]{10}\b|[0-9]{8,10}:[A-Za-z0-9_-]{35}|sk-[A-Za-z0-9]{20,}'; then
    echo "history contains something phone- or token-shaped" >&2
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
if git ls-files -z | xargs -0 grep -InE '/(Users|home)/[a-z]' ; then
    echo "the lines above carry someone's home directory" >&2
    fail personal-paths
fi

printf '\n'
if [ -n "$FAILED" ]; then
    echo "FAILED:$FAILED"
    exit 1
fi
echo "All security checks passed."
