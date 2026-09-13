# telegram-plugin

Read your own Telegram account from an AI coding agent. Find a chat, read its
history, follow a thread, search across chats, download attachments — then let
the agent do the actual work: summarise a conversation, pull out names, collect
every PDF from last month. The plugin supplies access to the data; nothing here
is a per-task script.

It speaks MTProto through [Telethon](https://docs.telethon.dev), as a *user*
client, so it sees what you see. That is the point: the Bot API cannot read
conversations you are already part of.

The runtime is a command-line program that prints one JSON object per call, plus
three Agent Skills that teach an agent when and how to call it. Any local agent
that can run an executable can use it — there is no server and no protocol to
speak.

Sending is off by default. There is no delete, leave, kick, forward or edit
command, and none is planned — see [Limits](#limits).

## Install

```bash
git clone https://github.com/kartollikaa/telegram-plugin.git
cd telegram-plugin
./scripts/setup.sh
```

`scripts/setup.sh` builds the private runtime without touching Telegram. Run it
once before the first session: the first dependency install takes a minute or
two, and doing it up front means your first real command answers immediately
instead of appearing to hang.

**Claude Code**, for one session, no installation:

```bash
claude --plugin-dir /absolute/path/to/telegram-plugin
```

or permanently, via a marketplace that lists this repository:

```bash
claude plugin marketplace add <your-marketplace>
claude plugin install telegram@<your-marketplace>
```

**Codex / OpenAI plugins.** The root [`plugin.json`](plugin.json) is an Agent
Plugins manifest, so the repository is shaped as a portable plugin directory and
skills are discovered from `skills/` automatically — the manifest does not, and
must not, list them. The manifest is validated against the published schema in
CI, but **the install has not been run against a Codex or ChatGPT host**, so
treat the host-side steps as untested. The absolute-path usage below works
regardless and is the path to fall back to.

**Any other local agent.** There is nothing to configure. Give the agent the
launcher's absolute path and let it run commands:

```bash
/absolute/path/to/telegram-plugin/bin/telegram whoami
/absolute/path/to/telegram-plugin/bin/telegram read @somechannel --limit 20
```

The three skills under [`skills/`](skills/) are plain Markdown with YAML front
matter; an agent that understands Agent Skills can load them as they are, and one
that cannot still has the `--help` output of every command.

**What does not work:** ChatGPT on the web or on a phone. This plugin runs a
Telegram client on your machine and keeps an authorised session file next to it,
so it needs a local executable environment. There is no hosted version, and an
agent without local shell access cannot use it at all.

Requirements: Python 3.10 or newer, with `python3 -m venv` available (on
Debian/Ubuntu that is the `python3-venv` package).

On its first run the launcher builds a virtualenv and installs Telethon, sending
every byte of that noise to stderr so stdout stays parseable JSON. The virtualenv
lives in the state directory, never in the plugin directory — a plugin directory
is replaced when the plugin updates.

The virtualenv lives inside the state directory, so pointing
`TELEGRAM_STATE_DIR` somewhere else — a second account, a throwaway test — gets
its own dependency install. Set `TELEGRAM_PLUGIN_PYTHON` to an interpreter that
already has Telethon if you would rather share one.

## Credentials

Create an application at https://my.telegram.org to get an API id and hash. Copy
[`.env.example`](.env.example) to the state directory as `.env` and fill both in:

```bash
mkdir -p ~/.local/state/telegram-plugin
cp .env.example ~/.local/state/telegram-plugin/.env
```

Anything exported in the host's environment wins over that file. Nothing secret
belongs in this repository or anywhere near your checkout.

## Log in

The session belongs to this plugin alone. **Never copy a `.session` file from
another application:** Telegram revokes an auth key used by two clients at once,
which breaks both of them.

**From inside a session, just ask:**

```
/telegram:login
```

The bundled skill drives the whole thing: it checks the state, installs the
dependencies if they are missing, then logs in by publishing a `tg://login` link
you confirm on a device already signed in to Telegram — or scan as a QR code
from **Settings → Devices → Link Desktop Device**. Nothing is typed, so no code
or password passes through the conversation, and no agent ever sees a credential.

The same thing by hand:

```bash
./bin/telegram-login --status     # is it authorised, and as whom?
./bin/telegram-login --qr         # publish a link and wait for it
./bin/telegram-login              # phone, code and password at a prompt
```

`--status` prints JSON and changes nothing. It also answers while another command
holds the session, saying it is in use rather than failing.

**Two-factor accounts finish in a terminal.** A confirmed link is not enough when
the account has a second factor, and a password must not travel through a tool
call — so `--qr` stops with `needs_password` and the plugin points you at
`bin/telegram-login`, which asks with hidden input. Nothing is ever accepted as a
command-line argument, so no secret lands in shell history or the process table.

Until a login succeeds, every command answers with the command to run rather than
a traceback.

Each command takes an exclusive lock on the session file, so two agents never race
for the auth key. The second waits rather than failing — see
[One account, several sessions](#one-account-several-sessions) for how long and what
happens when the wait runs out.

## Commands

Everything goes through one launcher, `bin/telegram`. Each invocation performs one
operation and prints one JSON object.

| Command | What it does |
|---|---|
| `whoami` | which account this session belongs to |
| `dialogs --query Q --limit N` | your chats, optionally filtered by title |
| `find-chat QUERY --limit N` | rank chats by a remembered name, with `score` and `matched_by` |
| `resolve CHAT` | identify a chat from a `t.me` link, `@name` or numeric id — never joins it |
| `message CHAT ID` | one exact message |
| `thread CHAT ROOT_ID` | replies in one forum topic or comment thread, paged on `--min-id` |
| `read CHAT …` | history in ascending id order, with a cursor |
| `search QUERY --chat CHAT …` | full-text search, in one chat or all of them |
| `download CHAT ID --dest-dir D` | one attachment to disk, returns the path |
| `send CHAT --text T` | **refused unless `TELEGRAM_PLUGIN_ALLOW_SEND=1`** |

`send` takes the body either inline with `--text` or from a file with `--text-file`
(a regular, non-symlink file under the output root) — the two are mutually exclusive.
Add `--reply-to MESSAGE_ID` to answer a specific message rather than posting a new one.

`read` accepts `--min-id`, `--max-id`, `--since`, `--until`, `--from-user` and
`--media-only`; `thread` accepts `--min-id` to continue past its first page; `search` inside one chat pages backwards on `--max-id`, because results arrive
newest first. A search **across all chats has no id cursor at all** — ids are only
ordered within a chat, so any cursor would silently drop every match above it.
Narrow it with `--chat`, or export it with `--out`. Both accept `--out` to write JSONL instead of returning rows.
`--since` and `--until` are inclusive at the instant they name, and a bare date
means midnight — so `--until 2026-01-03` stops before that day rather than
including it. Pass a time when you mean a whole day.

An empty result says whether the range was empty or the filters excluded
everything in it — the two are not the same answer.

Each message comes back with its id, an ISO date, the sender's id and display
name, the text, a link to the message, for replies the message it answers, and
for attachments the type, file name and size — never the bytes. Bytes arrive only
through `download`, one file per call.

A reply carries `reply_to`: `message_id`, a link to it, and `thread_id` for the
forum topic or comment thread it sits in. Two cases would otherwise mislead. In
a forum every message carries the header, so a post that answers nothing has a
`thread_id` and a null `message_id` rather than a reply to its own topic root.
And a reply can point into a different chat, where the link names that chat, or
is null — never an id read as belonging to this one.

### The JSON contract

One compact JSON object on stdout per call, followed by a newline. No diagnostic,
progress or dependency output ever joins it, so the result is safe to pipe straight
into a parser.

The two exceptions are the ones you would expect: `--help` and `--version` print
human text and exit `0`. Everything that touches Telegram answers in JSON.

| Exit code | Means | Where to look |
|---|---|---|
| `0` | success | the JSON object on stdout |
| `1` | the operation failed | `{"error": {"code": …, "message": …}}` on stdout |
| `2` | the command line was wrong | argparse usage on stderr; stdout is empty |

Error codes are stable strings, so an agent can branch on the code rather than on
prose. The complete set:

| Code | Means |
|---|---|
| `not_authorized` | no usable session; the message names the command to run |
| `missing_credentials` | no API id or hash in the environment or `.env` |
| `session_busy` | another process holds the session lock and the wait ran out |
| `unknown_chat_ref` | the chat reference is not one of the accepted forms |
| `not_a_member` | an invite link resolved, but the account has not joined |
| `message_not_found` | no message with that id in that chat |
| `no_such_media` | that message carries no attachment |
| `media_too_large` | the attachment exceeds `TELEGRAM_MAX_DOWNLOAD_BYTES` |
| `unsafe_path` | a read or write path escaped the output root |
| `invalid_timestamp` | `--since` or `--until` was not ISO 8601 |
| `send_disabled` | sending is off; nothing was sent |
| `empty_text` | the message body was empty or whitespace |
| `flood_wait` | Telegram asked for a wait; the message carries the seconds |
| `telegram_error` | a plugin error with no more specific code |
| `unexpected_error` | anything unclassified — treat as a bug |

## Skills

Three, all usable by name from inside a session:

| Skill | What it covers |
|---|---|
| `/telegram:read` | the flows — find a chat, read it, follow a thread, search, download, export — and the rules that keep results small |
| `/telegram:login` | authorises the session, or explains why it is not authorised |
| `/telegram:send` | the explicit send and reply path, and the rules around it |

## What you are handing over

Logging in creates a **full user session** on your account. That is not a bot
token with a narrow scope — it is the authority a Telegram client has. An agent
that can run the launcher can read everything the account can read: private
conversations, group history, channels you have joined, and the service messages
Telegram itself sends you. If other services deliver their login codes to your
Telegram, those are readable too.

- **Chat content reaches your model provider.** Every message a command returns
  becomes text in a conversation with a model running on someone else's
  computers, and most of it was written by people who never agreed to that. Read
  what you would be willing to paste in by hand, and prefer `--out`, which writes
  to your disk instead of into the conversation.
- **The session file is as sensitive as your password.** Anyone who copies it has
  the account until you revoke it, with no password or second factor in their
  way. `0700` on the directory and `0600` on the file protect it from other users
  of the machine — not from a backup. Keep `TELEGRAM_STATE_DIR` out of iCloud
  Drive, Dropbox, OneDrive, a synced Documents folder and any git repository, and
  remember that a whole-disk backup takes it wherever it lives.
- **Reading leaves no trace.** History read through this plugin is not marked
  read, so nobody in those chats sees anything. That is convenient, and it is
  also the reason not to set this up on somebody else's behalf.

**To revoke it:** in any Telegram client open Settings → Devices (Privacy and
Security → Active Sessions on some platforms), find the session and terminate it,
then delete the `.session` file. Do that when you stop using the plugin, if the
state directory is ever exposed, or whenever you are unsure — it costs nothing,
and `bin/telegram-login` gives you a new session in a minute.

Telegram's [API Terms of Service](https://core.telegram.org/api/terms) govern
what you may do with this access, and Telegram can limit or suspend an account
over activity that looks like automated bulk collection. This is your account and
your risk: read your own chats, and do not point this at an account that is not
yours.

## One account, several sessions

Telegram revokes an auth key used by two clients at once, so only one process may
hold the session at a time. That is not negotiable — but it does not have to mean
one *agent* at a time.

Each command **connects on demand and lets go after `TELEGRAM_IDLE_TIMEOUT`
seconds of inactivity**, and a call that finds the session busy **waits up to
`TELEGRAM_LOCK_WAIT` seconds** instead of failing. In practice: whoever asks
first works immediately, the others pause a moment. Coming back is cheap — the
session file already holds the auth key, so reconnecting is not a fresh
handshake.

If the wait runs out, the answer says which lock is held and what to do. The most
common cause is another Telegram command still running in another window; it will
let go by itself once it finishes.

## Limits

These are deliberate. A command that empties five hundred messages into a context
window is useless, and a plugin holding a personal session should not be able to
do damage on a misread instruction.

- **`--limit` is capped**, so an over-large request is refused rather than quietly
  trimmed: 200 for `read`, `search`, `thread` and `dialogs`, and 50 for `find-chat`, whose
  job is to hand you a shortlist. Message text is truncated at 500 characters and
  flagged.
- **Wide ranges go to disk.** Pass `--out` and the rows are written as JSONL; the
  reply is a path, a line count and an id range. `--out-limit` bounds that export
  (default 1000, maximum 5000). **It stops at the limit without saying so** — the
  reply looks the same as a complete export, so compare `lines` against what you
  expected before treating a dump as the whole range.
- **Paging is by cursor, where one exists.** `read` moves forward: continue with
  `--min-id`. `search` in one chat moves backward: continue with `--max-id`. A
  global search returns `next_cursor: null` and says why. You do not have to
  remember which: the `note` on every continuable page names the flag to pass.
- **A capped scan is not an exhausted range.** `since`, `until` and `--media-only`
  are applied here rather than by Telegram, so a wide range is scanned in bounded
  steps; when the scan stops at its ceiling the reply keeps `has_more` true and
  hands back the last id it looked at, even if the filters accepted nothing.
- **An export says whether it is complete.** `--out` returns `complete`, and when
  false a note with the point to resume from.
- **Writes are confined.** `--out` and `--dest-dir` must stay inside
  `TELEGRAM_OUTPUT_ROOT` (by default the state directory's `downloads/`), and an
  existing file is never overwritten silently.
- **Attachments have a size ceiling** (`TELEGRAM_MAX_DOWNLOAD_BYTES`), checked
  before anything is downloaded, and a sender-chosen file name is stripped of
  shell metacharacters and separators before it reaches the agent as a path.
- **Sending is explicit and echoed.** It is refused unless
  `TELEGRAM_PLUGIN_ALLOW_SEND=1`; one invocation sends one message, and every
  result returns the actual message id plus the resolved recipient id and title.
- **No destructive commands exist.** Not gated — absent. Deleting, leaving,
  kicking, forwarding and editing are things you do in a Telegram client.
- **Resolving an invite link never joins the chat.** If the account is not a
  member, you are told so.
- **Message content is data, not instructions.** Everything the commands return
  was written by other people; the bundled skills instruct the agent to treat it
  as data and never to act because a message asked it to.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `TELEGRAM_API_ID` | from my.telegram.org | required |
| `TELEGRAM_API_HASH` | from my.telegram.org | required |
| `TELEGRAM_STATE_DIR` | session, `.env`, virtualenv, downloads | `~/.local/state/telegram-plugin` |
| `TELEGRAM_SESSION_NAME` | session file basename | `telegram` |
| `TELEGRAM_OUTPUT_ROOT` | where commands may write | `$TELEGRAM_STATE_DIR/downloads` |
| `TELEGRAM_MAX_DOWNLOAD_BYTES` | refuse attachments above this | `104857600` (100 MiB) |
| `TELEGRAM_PLUGIN_PYTHON` | interpreter override, skips the virtualenv | unset |
| `TELEGRAM_PLUGIN_ALLOW_SEND` | `1` allows `send` | unset |
| `TELEGRAM_IDLE_TIMEOUT` | seconds of inactivity before the account is released | `60` |
| `TELEGRAM_LOCK_WAIT` | seconds to wait for a session another process holds | `20` |

The state directory is created `0700`, and `.env` and the session file `0600`.

### Turning sending on

`TELEGRAM_PLUGIN_ALLOW_SEND=1` is an escalation, not a convenience. Without it,
the worst a confused or manipulated agent can do is read, and write files inside
one directory. With it, an agent can send messages from your account, under your
name, to anyone the account can reach — while deciding what to send partly from
text other people wrote. Nothing can un-send a message.

The launcher narrows each invocation to one message and echoes the actual
recipient and message id. What stops a manipulated agent from sending is mostly
the instruction in the bundled skill, in the same context window as the
attacker's text. So set the variable for the one session that needs it rather
than in your shell profile:

```bash
TELEGRAM_PLUGIN_ALLOW_SEND=1 claude
```

## Development

```bash
python3 -m venv .venv && ./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest          # no network required
./scripts/security-check.sh           # lint, SAST, dependency audit, secret sweep
```

The test suite needs no network. The security script does: `pip-audit` queries a
vulnerability database. It also wants `shellcheck` (`brew install shellcheck`,
`apt-get install shellcheck`); without it that one check is skipped locally and
enforced in CI.

The command-line layer is intentionally thin; the parts worth testing are
ordinary functions. See [docs/design.md](docs/design.md) for why each piece is
shaped the way it is.

## A note on names

This project is not published on PyPI and has no package there. If you
`pip install` something named after this repository, it is not this code — clone
the repository instead. Several unrelated projects use similar names to bridge
Telegram and agents in the opposite direction, letting a bot message *you*; none
of them is a copy or a fork of this one.

## License

MIT — see [LICENSE](LICENSE).
