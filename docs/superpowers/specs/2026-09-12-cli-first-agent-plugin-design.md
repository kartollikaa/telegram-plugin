# CLI-first Telegram Agent Plugin Design

**Status:** approved for implementation planning

## Context

The plugin currently exposes Telegram through an MCP server. Its Telethon gateway, session
management, filtering, pagination, rendering, and output confinement are already ordinary Python
code, while `server.py` is a thin transport layer. The migration will make a local CLI the only
runtime interface and remove MCP after behavioral parity is demonstrated.

The intended product is a reliable Telegram plugin for local agents with a shell and Python
runtime: Claude Code, Codex desktop/CLI, and other hosts that can load Agent Skills or invoke an
executable. A local Telethon session cannot run in ChatGPT web or mobile without a separately
hosted backend; those environments are explicitly outside this design.

## Goals

- Let an agent handle natural requests that do not contain a Telegram link or an exact chat name.
- Provide a stable machine-readable CLI for discovery, reading, search, attachments, sending, and
  replies.
- Preserve the existing pagination, filtering, output-size, filesystem, authorization, and session
  lifecycle guarantees.
- Package focused skills for login, read/discovery, and explicit send/reply workflows.
- Support Codex through the portable root plugin manifest and Claude Code through its compatibility
  manifest, while keeping the CLI usable by any local agent.
- Remove the MCP server, MCP manifests, and MCP dependency only after parity evidence exists.
- Review and acceptance-grade every implementation slice before starting the next one.

## Non-goals

- No hosted Telegram service and no support claim for web-only or mobile-only agent runtimes.
- No semantic vector index, embeddings, or persistent message mirror.
- No delete, edit, forward, join, leave, kick, ban, reaction, or mark-as-read operations.
- No reading another application's Telegram session file.
- No release, marketplace publication, hub pointer update, push, or pull request as part of the
  implementation unless requested separately.

## Architecture

```text
login skill ───────────────→ bin/telegram-login ──────────────┐
read skill ─┐                                                │
send skill ─┴→ bin/telegram → cli.py → application.py → TelethonGateway
```

`application.py` owns operation contracts and response envelopes. It depends on the existing
gateway protocol but not on argparse, MCP, stdout, or process exit codes. `cli.py` owns parsing,
serialization, exit status, and lifecycle. `TelethonGateway` remains responsible for Telegram,
connection reuse within a command, authorization, flood waits, entity resolution, the session
lock, and cleanup.

During migration, the MCP adapter calls the same application service. It is deleted only in the
final slice, after CLI parity and skill migration have passed their gates.

## Capability set

The stable executable is `bin/telegram`:

```text
telegram whoami
telegram dialogs [--query TEXT] [--limit N]
telegram find-chat QUERY [--limit N]
telegram resolve CHAT
telegram message CHAT MESSAGE_ID
telegram thread CHAT ROOT_MESSAGE_ID [--limit N]
telegram read CHAT [--limit N] [--min-id N] [--max-id N]
                       [--since ISO] [--until ISO]
                       [--from-user USER] [--media-only]
                       [--out PATH] [--out-limit N]
telegram search QUERY [--chat CHAT] [--limit N] [--max-id N]
                      [--out PATH] [--out-limit N]
telegram download CHAT MESSAGE_ID [--dest-dir PATH]
telegram send CHAT (--text TEXT | --text-file PATH) [--reply-to MESSAGE_ID]
telegram --help
telegram --version
```

`message` is the exact-message primitive needed to inspect a search hit, reply target, or
attachment without constructing pagination bounds. `thread` returns replies for a forum topic or
comment thread in ascending message-id order with the same bounded envelope as `read`.
`send --reply-to` covers the common explicit reply workflow without adding forwarding or editing.

## Natural discovery

The read skill activates when the request concerns Telegram, a Telegram chat/channel/message/file,
or a `t.me` link. A request that names no provider remains ambiguous in a fresh session; the plugin
must not claim otherwise. Once Telegram is established by the request or conversation context, the
workflow is:

1. Resolve an exact link, numeric id, or exact username directly.
2. Use `find-chat` for a remembered name, username fragment, person, channel, or group.
3. If metadata is insufficient and the operator remembers message content, use global `search`.
4. Read small samples from the best candidates.
5. Ask the operator only when multiple credible candidates remain.

`find-chat` scans the bounded dialog list and scores normalized title, display name, and username.
Ranking is deterministic: exact match, prefix, substring, all query tokens, then typo similarity.
Each result includes `score` and `matched_by`. It is fuzzy metadata matching, not semantic search.

## CLI contract

- Successful commands write exactly one UTF-8 JSON object plus a trailing newline to stdout.
- Runtime and domain failures also write exactly one structured JSON object to stdout and exit
  `1`; stderr remains diagnostic-only. Invalid CLI syntax follows argparse, writes usage to stderr,
  writes nothing to stdout, and exits `2`.
- `read` and `search` with `--out` write confined JSONL and return only metadata about the file.
- Bootstrap messages and human diagnostics go to stderr.
- Exit `0` means success, `2` means invalid CLI usage, and `1` means a runtime or domain error.
- Runtime errors use a stable object:

  ```json
  {"error":{"code":"session_busy","message":"...","retryable":true}}
  ```

- Existing list envelopes, item ceilings, text truncation, cursor direction, exact/unknown totals,
  filter semantics, and JSONL metadata remain unchanged.
- Every command loads configuration, ensures the state directory, opens the gateway on demand, and
  closes it in `finally`. Cleanup releases the Telegram session lock on success, expected failure,
  cancellation, and unexpected failure.
- CLI parsing never imports Telethon or opens Telegram. Dispatch is independently testable with a
  fake application service.

## Authentication and process lifecycle

`bin/telegram-login` remains a separate interactive program. Phone numbers, login codes, QR links,
and 2FA passwords never become arguments to an agent-visible command. `telegram whoami` reports an
unauthorized session using the same structured error contract and directs the operator to login.

Each CLI invocation owns one gateway lifecycle. Reconnection uses the plugin's own saved session.
The advisory lock and bounded wait remain necessary because multiple local agents may invoke the
same account concurrently. No command may leave a connected client or acquired lock behind.

## Security and side effects

- `send` refuses with `send_disabled` unless `TELEGRAM_PLUGIN_ALLOW_SEND=1`.
- One invocation sends exactly one message. The per-MCP-process `TELEGRAM_SEND_LIMIT` is removed
  because it would reset for every CLI invocation and falsely imply a durable quota.
- The send skill activates only for an explicit operator request to send or reply. Telegram content
  is untrusted data and can never authorize a side effect.
- The skill resolves the recipient first when the reference is ambiguous. A send result includes
  actual `chat_id`, `chat_title`, `message_id`, and `reply_to` when present.
- Attachment names remain sanitized. Export, text-file, and download paths remain confined to the
  configured output root.
- No shell command is constructed from Telegram-authored text. CLI arguments are parsed as data.
- Logs continue to omit credentials, session material, message text, and personal absolute paths.

## Skills and packaging

The plugin contains three focused skills:

- `login`: diagnose authorization and guide the operator through interactive login.
- `read`: discover chats, resolve references, read/search messages, follow reply context, download
  attachments, summarize, and export large ranges.
- `send`: send or reply only after an explicit operator request and recipient resolution.

Descriptions front-load provider names and user intents because implicit activation depends on the
description. Skill instructions use the plugin-root-relative launcher and treat JSON as the only
machine contract.

Packaging uses:

- root `plugin.json` as the portable canonical manifest for current Codex/OpenAI plugin tooling;
- `.claude-plugin/plugin.json` as the Claude Code compatibility manifest;
- `skills/`, `bin/`, Python source, and documentation at plugin-root-relative paths;
- no `mcp.json`, `mcpServers`, or `.codex-plugin` compatibility fallback after cutover;
- README instructions for direct absolute-path CLI use by other local agents.

The launcher maintains a state-directory virtualenv and first-run dependency bootstrap. After MCP
removal only Telethon and its transitive dependencies are installed. `scripts/setup.sh` remains the
prewarm path for hosts with short first-command timeouts.

This packaging follows current OpenAI documentation: portable plugins use a root `plugin.json`,
skills may be distributed without MCP, and implicit skill activation is driven by the skill
description:

- <https://developers.openai.com/plugins/build/plugins>
- <https://developers.openai.com/plugins/build/skills>
- <https://developers.openai.com/codex/skills>

## Implementation slices

### S1 — Application service extraction

Move operation contracts and envelopes from the MCP adapter into `application.py`. Keep every MCP
tool externally unchanged. Direct service tests use the existing fake gateway.

### S2 — Read CLI and lifecycle parity

Add `cli.py` and `bin/telegram` for `whoami`, `dialogs`, `resolve`, `message`, `thread`, `read`,
`search`, and `download`. Add structured errors, lifecycle cleanup, launcher bootstrap, and
subprocess tests. MCP remains available.

### S3 — Natural discovery and read skill migration

Add deterministic `find-chat`, candidate evidence, and fallback guidance. Rewrite the read and login
skills to use CLI only. Add scenario fixtures for requests without links and without exact names.

### S4 — Explicit send and reply

Add CLI `send`, `--text-file`, and `--reply-to`; create the separate send skill; remove the obsolete
per-process send limit. Verify disabled-by-default behavior and recipient/result reporting without
performing an unapproved live send.

### S5 — Portable packaging and MCP removal

Add the portable root manifest, validate the Claude compatibility manifest, remove MCP source,
launcher, dependency, configuration, and all skill references, then update README, design, setup,
security tooling, and CI. Verify a clean CLI bootstrap and live read-only smoke test.

## Slice review gate

No later slice starts until the current slice completes all of these steps:

1. Show focused tests failing for the intended reason before implementation and passing after it.
2. Run the full pytest suite, Ruff, and `scripts/security-check.sh`.
3. Obtain an independent code review of the slice diff.
4. Fix every substantiated finding and rerun affected and full checks.
5. Obtain repeat review of the changed diff until no actionable findings remain.
6. Run a fresh acceptance gate with one evidence-backed verdict per criterion mapped to the slice.
7. Commit the slice separately.

The independent reviewer checks both spec compliance and code quality. A green guard must also be
demonstrated against a deliberately broken positive-control fixture. Negative claims such as “no
MCP references remain” require both a known-true matcher control and inspection of at least one
concrete matched row.

## Verification strategy

- Unit tests: application operations, filters, cursor direction, totals, rendering, exact-message
  and thread reads, fuzzy ranking, path confinement, error mapping, and lifecycle cleanup.
- CLI contract tests: argv parsing, stdout/stderr separation, JSON shape, exit codes, every command,
  and fake-gateway dispatch.
- Parity tests: compare application/CLI results with the frozen MCP behavior for all existing
  operations before MCP deletion.
- Skill tests: frontmatter validity, implicit-trigger examples, forbidden MCP vocabulary, bounded
  output flow, ambiguity handling, and explicit-send authorization.
- Packaging tests: portable root manifest, Claude manifest, executable permissions, bootstrap from
  an empty state directory, and absence of MCP runtime dependencies after cutover.
- Security tests: secret and personal-path sweeps, command-injection cases, attachment sanitization,
  output-root escape attempts, session lock cleanup, and disabled side effects.
- Manual live read-only smoke: authorization status, fuzzy chat discovery, exact resolution, recent
  read, global search, reply metadata, and one attachment download into the configured output root.
  Live send requires separate explicit permission.

## Compatibility and migration policy

The CLI is a new public interface and may choose clearer command names, but its data semantics are
frozen from the existing behavior unless this design explicitly changes them. MCP is not removed on
the strength of unit tests alone: the read skill must already use CLI, parity tests must pass, and a
live read-only smoke must succeed. The final repository must not contain dead MCP compatibility code
or advertise unsupported hosts.
