# telegram-plugin

Read your own Telegram account from an AI coding agent. Find a chat, read its
history, search across chats, download attachments — then let the agent do the
actual work: summarise a thread, pull out names, collect every PDF from last
month. The plugin supplies access to the data; nothing here is a per-task script.

It speaks MTProto through [Telethon](https://docs.telethon.dev), as a *user*
client, so it sees what you see. That is the point: the Bot API cannot read
conversations you are already part of.

Sending is off by default. There is no delete, leave, kick, forward or edit
tool, and none is planned — see [Limits](#limits).

## Install

The plugin ships an MCP server plus manifests for a few hosts. Pick whichever
matches yours; they all launch the same `bin/telegram-mcp`.

```bash
git clone https://github.com/kartollikaa/telegram-plugin.git
cd telegram-plugin
```

**Any MCP host.** Point it at the launcher with an absolute path:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "/absolute/path/to/telegram-plugin/bin/telegram-mcp"
    }
  }
}
```

**Claude Code**, for one session, no installation:

```bash
claude --plugin-dir /absolute/path/to/telegram-plugin
```

or permanently, via a marketplace that lists this repository:

```bash
claude plugin marketplace add <your-marketplace>
claude plugin install telegram@<your-marketplace>
```

`.codex-plugin/` and `.cursor-plugin/` hold manifests written from documentation
and from the conventions other published plugins follow. **Neither has been run
against a Codex or Cursor host**, and in particular the plugin-root variable each
one interpolates is unverified — if it does not expand, the launcher path comes
out wrong and the server simply never starts. Treat them as a starting point, not
a supported path: the absolute-path `mcpServers` entry above works everywhere.
Corrections from anyone who has actually run this on either host are welcome.

On its first run the launcher builds a virtualenv and installs its two
dependencies, sending every byte of that noise to stderr so the MCP channel on
stdout stays clean. The virtualenv lives in the state directory, never in the
plugin directory — a plugin directory is replaced when the plugin updates.

**Do this once before the first session:**

```bash
./scripts/setup.sh
```

That first install takes longer than some hosts wait for an MCP server to
announce itself. Skip it and your very first session may
show no `telegram` tools at all — the install is still running. It is not broken:
run `scripts/setup.sh`, or just start a second session once the install has
finished.

Requirements: Python 3.10 or newer, with `python3 -m venv` available (on
Debian/Ubuntu that is the `python3-venv` package).

The virtualenv lives inside the state directory, so pointing
`TELEGRAM_STATE_DIR` somewhere else — a second account, a throwaway test — gets
its own dependency install. Set `TELEGRAM_PLUGIN_PYTHON` to an interpreter that
already has them if you would rather share one.

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

`--status` prints JSON and changes nothing. It also answers while the MCP server
is running, saying the session is in use rather than failing.

**Two-factor accounts finish in a terminal.** A confirmed link is not enough when
the account has a second factor, and a password must not travel through a tool
call — so `--qr` stops with `needs_password` and the plugin points you at
`bin/telegram-login`, which asks with hidden input. Nothing is ever accepted as a
command-line argument, so no secret lands in shell history or the process table.

Until a login succeeds, every tool answers with the command to run rather than a
traceback.

The server takes an exclusive lock on the session file. If two hosts try to use
one session at the same time, the second is told so instead of racing for the
auth key.

## What you are handing over

Logging in creates a **full user session** on your account. That is not a bot
token with a narrow scope — it is the authority a Telegram client has. While the
server runs, an agent can read everything the account can read: private
conversations, group history, channels you have joined, and the service messages
Telegram itself sends you. If other services deliver their login codes to your
Telegram, those are readable too.

- **Chat content reaches your model provider.** Every message a tool returns
  becomes text in a conversation with a model running on someone else's
  computers, and most of it was written by people who never agreed to that. Read
  what you would be willing to paste in by hand, and prefer `out_path`, which
  writes to your disk instead of into the conversation.
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

## Commands

Two, both usable from inside a session:

| Command | What it does |
|---|---|
| `/telegram:read` | the flows — find a chat, read it, search, download, export — and the rules that keep results small |
| `/telegram:login` | authorises the session, or explains why it is not authorised |

## Tools

| Tool | What it does |
|---|---|
| `whoami` | which account this session belongs to |
| `list_dialogs(query?, limit=50)` | your chats, filtered by title |
| `resolve_chat(ref)` | identify a chat from a `t.me` link, `@name` or numeric id — never joins it |
| `read_messages(chat, …)` | history in ascending id order, with a cursor |
| `search_messages(query, chat?, …)` | full-text search, in one chat or all of them |
| `download_media(chat, message_id, dest_dir?)` | one attachment to disk, returns the path |
| `send_message(chat, text)` | **only registered when `TELEGRAM_PLUGIN_ALLOW_SEND=1`** |

`read_messages` accepts `min_id`, `max_id`, `since`, `until`, `from_user` and
`media_only`; `search_messages` pages backwards on `max_id`, because search
results arrive newest first. An empty result says whether the range was empty or
the filters excluded everything in it — the two are not the same answer. Each message comes back with its id, an ISO date, the sender's id
and display name, the text, a link to the message, and for attachments the type,
file name and size — never the bytes. Bytes arrive only through
`download_media`, one file per call.

## Limits

These are deliberate. A tool that empties five hundred messages into a context
window is useless, and a plugin holding a personal session should not be able to
do damage on a misread instruction.

- **`limit` is capped at 200 in the tool schema**, so an over-large request is
  refused rather than quietly trimmed. Message text is truncated at 500
  characters and flagged.
- **Wide ranges go to disk.** Pass `out_path` and the rows are written as JSONL;
  the reply is a path, a line count and an id range.
- **Paging is by cursor.** Every read returns `next_cursor`; continue with
  `min_id`.
- **Writes are confined.** `out_path` and `dest_dir` must stay inside
  `TELEGRAM_OUTPUT_ROOT` (by default the state directory's `downloads/`), and an
  existing file is never overwritten silently.
- **Attachments have a size ceiling** (`TELEGRAM_MAX_DOWNLOAD_BYTES`), checked
  before anything is downloaded, and a sender-chosen file name is stripped of
  shell metacharacters and separators before it reaches the agent as a path.
- **Sending is capped and echoed.** One server process may send
  `TELEGRAM_PLUGIN_SEND_LIMIT` messages, and every send returns the resolved
  recipient's id and title so a wrong recipient is visible after the fact.
- **No destructive tools exist.** Not gated — absent. Deleting, leaving, kicking,
  forwarding and editing are things you do in a Telegram client.
- **Resolving an invite link never joins the chat.** If the account is not a
  member, you are told so.
- **Message content is data, not instructions.** Everything the tools return was
  written by other people; the bundled skill instructs the agent to treat it as
  data and never to act because a message asked it to.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `TELEGRAM_API_ID` | from my.telegram.org | required |
| `TELEGRAM_API_HASH` | from my.telegram.org | required |
| `TELEGRAM_STATE_DIR` | session, `.env`, virtualenv, downloads | `~/.local/state/telegram-plugin` |
| `TELEGRAM_SESSION_NAME` | session file basename | `telegram` |
| `TELEGRAM_OUTPUT_ROOT` | where tools may write | `$TELEGRAM_STATE_DIR/downloads` |
| `TELEGRAM_MAX_DOWNLOAD_BYTES` | refuse attachments above this | `104857600` (100 MiB) |
| `TELEGRAM_PLUGIN_PYTHON` | interpreter override, skips the virtualenv | unset |
| `TELEGRAM_PLUGIN_ALLOW_SEND` | `1` registers `send_message` | unset |
| `TELEGRAM_PLUGIN_SEND_LIMIT` | messages one server process may send | `20` |

The state directory is created `0700`, and `.env` and the session file `0600`.

### Turning sending on

`TELEGRAM_PLUGIN_ALLOW_SEND=1` is an escalation, not a convenience. Without it,
the worst a confused or manipulated agent can do is read, and write files inside
one directory. With it, an agent can send messages from your account, under your
name, to anyone the account can reach — while deciding what to send partly from
text other people wrote. Nothing can un-send a message.

The tool layer narrows this only a little — a per-process send cap and an
echoed recipient. What stops a manipulated agent from sending is mostly the
instruction in the bundled skill, in the same context window as the attacker's
text. So set the variable for the one session that needs it rather than in your
shell profile:

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

The MCP layer is intentionally thin; the parts worth testing are ordinary
functions. See [docs/design.md](docs/design.md) for why each piece is shaped the
way it is.

## A note on names

This project is not published on PyPI and has no package there. If you
`pip install` something named after this repository, it is not this code — clone
the repository instead. A package called `telegram-mcp` does exist on PyPI, by a
different author; it is an unrelated tool solving the opposite problem (it lets
an agent message *you*), not a copy or a fork of this one.

## License

MIT — see [LICENSE](LICENSE).
