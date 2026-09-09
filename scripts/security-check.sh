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
# pip-audit exits non-zero both when it finds something and when it cannot reach
# the advisory database. Those are different facts, and reporting a network
# hiccup as a finding is how people learn to ignore a red run.
audit_output=$(mktemp)
audit_attempt=1
while : ; do
    if "$PYTHON" -m pip_audit --progress-spinner off >"$audit_output" 2>&1; then
        cat "$audit_output"
        break
    fi
    if grep -qiE 'connection|timed out|temporarily unavailable|network is unreachable|resolve' \
        "$audit_output"; then
        if [ "$audit_attempt" -lt 3 ]; then
            echo "could not reach the advisory database, retrying ($audit_attempt/3)" >&2
            audit_attempt=$((audit_attempt + 1))
            sleep 3
            continue
        fi
        cat "$audit_output" >&2
        echo "pip-audit could not reach the advisory database after 3 attempts —" >&2
        echo "this is inconclusive, not a clean bill of health." >&2
        fail pip-audit-unreachable
        break
    fi
    cat "$audit_output"
    fail pip-audit
    break
done
rm -f "$audit_output"

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

# Values named in .security-allowlist are known-innocent. The file itself is not
# scanned: it exists to hold exactly the strings these patterns match.
ALLOWLIST="$ROOT/.security-allowlist"
FILTER=$(mktemp)
trap 'rm -f "$FILTER"' EXIT
if [ -f "$ALLOWLIST" ]; then
    grep -v '^[[:space:]]*#' "$ALLOWLIST" | grep -v '^[[:space:]]*$' >"$FILTER" || true
fi

drop_allowlisted() {
    if [ -s "$FILTER" ]; then grep -vF -f "$FILTER" || true; else cat; fi
}

note "secret sweep — tracked files"
TREE_HITS=$(git grep -InE "$SHAPES" -- . ':(exclude).security-allowlist' | drop_allowlisted)
if [ -n "$TREE_HITS" ]; then
    printf '%s\n' "$TREE_HITS" >&2
    echo "the lines above look like credentials" >&2
    fail secrets-in-tree
fi

note "secret sweep — nothing secret is tracked at all"
if git ls-files | grep -E '(^|/)\.env$|\.session(-journal|-wal|-shm)?$'; then
    echo "the files above must never be tracked" >&2
    fail secret-files-tracked
fi

# History carries what the working tree no longer shows: a credential that was
# committed and then removed passes every tree-level check. So the history sweep
# uses the same shapes, bare hex included, and known-innocent values are named
# explicitly in .security-allowlist rather than left as a blind spot.
note "secret sweep — history"
# Only added/removed diff lines: `git log -p` prints each commit's own 40-hex
# SHA in its header, whose first 32 characters match the api-hash shape.
HISTORY_HITS=$(
    git log -p --all --format='' | grep '^[+-]' | grep -ohE "$SHAPES" | sort -u |
        { if [ -s "$FILTER" ]; then grep -vixFf "$FILTER" || true; else cat; fi; }
)
if [ -n "$HISTORY_HITS" ]; then
    echo "history contains credential-shaped strings that are not allowlisted:" >&2
    printf '%s\n' "$HISTORY_HITS" | sed 's/./*/g' >&2
    echo "(masked above; find them with: git log -p --all | grep -nE \"\$SHAPES\")" >&2
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
