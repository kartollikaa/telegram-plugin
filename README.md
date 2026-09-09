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

`.codex-plugin/` and `.cursor-plugin/` carry the same manifest for Codex and
Cursor. Only the Claude Code path and the plain `mcpServers` entry above have
been exercised against a running host; if your host disagrees with its manifest,
the absolute-path entry always works.

There is no setup step. On its first run the launcher builds a virtualenv and
installs its two dependencies, sending every byte of that noise to stderr so the
MCP channel on stdout stays clean. The virtualenv lives in the state directory,
never in the plugin directory — a plugin directory is replaced when the plugin
updates. Run `scripts/setup.sh` if you would rather do it up front.

Requirements: Python 3.10 or newer, with `python3 -m venv` available (on
Debian/Ubuntu that is the `python3-venv` package).

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

```bash
./bin/telegram-login
```

It asks for a phone number, the code Telegram sends, and a two-factor password
if the account has one. The password prompt is hidden, and no secret is accepted
as a command-line argument — nothing lands in shell history or the process
table. Because it is interactive, run it yourself in a terminal; an agent cannot
do it for you. Until it succeeds, every tool answers with the command to run
rather than a traceback.

The server takes an exclusive lock on the session file. If two hosts try to use
one session at the same time, the second is told so instead of racing for the
auth key.

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
`media_only`. Each message comes back with its id, an ISO date, the sender's id
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
| `TELEGRAM_PLUGIN_PYTHON` | interpreter override, skips the virtualenv | unset |
| `TELEGRAM_PLUGIN_ALLOW_SEND` | `1` registers `send_message` | unset |

The state directory is created `0700`, and `.env` and the session file `0600`.

## Development

```bash
python3 -m venv .venv && ./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest          # no network required
./scripts/security-check.sh           # lint, SAST, dependency audit, secret sweep
```

The MCP layer is intentionally thin; the parts worth testing are ordinary
functions. See [docs/design.md](docs/design.md) for why each piece is shaped the
way it is.

## A note on names

This project is not published on PyPI. A package called `telegram-mcp` exists
there and is unrelated to any of the well-known Telegram MCP servers — installing
it would hand your API hash and session to a third party. Clone this repository
instead.

## License

MIT — see [LICENSE](LICENSE).
