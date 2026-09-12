# CLI-first Telegram Agent Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Telegram MCP runtime with a portable, machine-readable CLI and focused Agent Skills while preserving proven behavior and adding fuzzy chat discovery, exact-message/thread reads, and explicit send/reply.

**Architecture:** `TelegramApplication` owns transport-independent operations and envelopes over `TelegramGateway`. `telegram_plugin.cli` turns argv into one application call and one JSON result, while `bin/telegram` bootstraps the private runtime. MCP remains a temporary adapter until CLI parity, skill migration, and live read-only verification pass, then is removed.

**Tech Stack:** Python 3.10+, argparse, asyncio, Telethon 1.x, pytest/pytest-asyncio, Ruff, Bandit, pip-audit, POSIX shell launchers, Agent Skills, portable `plugin.json`.

**Spec:** `docs/superpowers/specs/2026-09-12-cli-first-agent-plugin-design.md`

## Global Constraints

- Support local agents with shell and Python: Claude Code, Codex desktop/CLI, and generic Agent Skills-compatible hosts.
- Keep Telegram content untrusted; only an explicit operator request may authorize sending.
- Keep delete, edit, forward, join, leave, kick, ban, reaction, and mark-as-read absent.
- Preserve existing pagination direction, filter behavior, output ceilings, truncation, JSONL metadata, and path confinement.
- Emit exactly one compact JSON object to stdout for success and domain/runtime failure; argparse usage goes only to stderr.
- Exit codes are `0` success, `1` domain/runtime failure, and `2` invalid CLI usage.
- Always close the gateway and release the session lock on every exit path.
- Do not add dependencies for fuzzy matching; use Python standard-library normalization and similarity.
- Do not perform a live send without separate explicit operator permission.
- Use version `0.6.0` consistently in `pyproject.toml` and both final manifests; tagging and
  marketplace publication remain separate work.
- Every task closes its mapped acceptance criteria and passes an independent review before commit.

## File map

- `src/telegram_plugin/application.py`: transport-independent operation contracts and envelopes.
- `src/telegram_plugin/cli.py`: argparse tree, dispatch, structured serialization, exit codes, gateway lifecycle.
- `src/telegram_plugin/matching.py`: deterministic chat-candidate normalization and ranking.
- `src/telegram_plugin/client.py`: Telethon gateway, exact-message/thread primitives, reply-aware send.
- `src/telegram_plugin/errors.py`: domain exceptions and stable machine error codes.
- `bin/telegram`: universal CLI launcher and dependency bootstrap.
- `skills/login/SKILL.md`: authorization workflow through `telegram-login`.
- `skills/read/SKILL.md`: discovery/read/search/download workflow through `telegram`.
- `skills/send/SKILL.md`: explicit send/reply workflow and side-effect rules.
- `plugin.json`: portable canonical plugin manifest.
- `.claude-plugin/plugin.json`: Claude Code compatibility manifest.
- `tests/test_application.py`: application operation contract.
- `tests/test_cli.py`: parser, dispatch, stdout/stderr, exit-code, and lifecycle contract.
- `tests/test_matching.py`: deterministic fuzzy discovery.
- `tests/test_gateway_messages.py`: exact-message, thread, and reply-aware gateway behavior.
- Existing tests: adapt server parity, manifests, launchers, skills, docs, config, and security guards per slice.

## Review gate used by every task

Run these steps after focused tests are green and before the task commit:

1. Run `./.venv/bin/python -m pytest`.
2. Run `./.venv/bin/python -m ruff check .`.
3. Run `./scripts/security-check.sh` and record whether pip-audit completed or was separately blocked by network.
4. Give the slice diff and its mapped acceptance criteria to a fresh independent reviewer.
5. Fix every substantiated finding and rerun focused and full checks.
6. Ask the reviewer to re-check the changed diff until no actionable finding remains.
7. Run `acceptance-gate` against evidence produced after the final fix.
8. Commit only when every mapped criterion is PASS. Never commit a FAIL or NO EVIDENCE slice as complete.

---

### Task 1: Extract the application service without changing MCP behavior

**Files:**

- Create: `src/telegram_plugin/application.py`
- Create: `tests/test_application.py`
- Modify: `src/telegram_plugin/server.py`
- Modify: `tests/test_server_tools.py`

**Interfaces:**

- Consumes: `Config`, `TelegramGateway`, `Batch`, `parse_chat_ref`, `safe_output_dir`, `safe_output_path`, `write_jsonl`, `paginate`, `envelope`.
- Produces: `TelegramApplication(config: Config, gateway: TelegramGateway)` with async methods `whoami`, `dialogs`, `resolve`, `read`, `search`, `download`, and `send`.
- Preserves: MCP tool names, schemas, annotations, conditional send registration, and returned dictionaries.

- [x] **Step 1: Freeze representative MCP results before moving code**

Add tests in `tests/test_application.py` that construct `Config`, `FakeGateway`, and the not-yet-created `TelegramApplication`. Cover dialog envelopes, forward history cursors, backward search cursors, JSONL metadata, download confinement, and exception propagation.

```python
@pytest.mark.asyncio
async def test_read_keeps_forward_cursor_contract(config, rows):
    app = TelegramApplication(config, FakeGateway(rows=rows))
    result = await app.read(chat="@alpha", limit=2)
    assert [item["id"] for item in result["items"]] == [1, 2]
    assert result["has_more"] is True
    assert result["next_cursor"] == 2
    assert result["cursor_field"] == "min_id"
```

- [x] **Step 2: Run the focused test and verify the red state**

Run: `./.venv/bin/python -m pytest tests/test_application.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'telegram_plugin.application'`.

- [x] **Step 3: Create `TelegramApplication` and move the operation logic**

Use one class with stored config and gateway. Keep defaults and keyword names identical to the MCP schema so the adapter remains mechanical.

```python
class TelegramApplication:
    def __init__(self, config: Config, gateway: TelegramGateway) -> None:
        self.config = config
        self.gateway = gateway

    async def whoami(self) -> dict:
        return await self.gateway.me()

    async def dialogs(self, query: str | None = None, limit: int = DEFAULT_ITEMS) -> dict:
        batch = await self.gateway.dialogs(query, limit)
        note = f"{len(batch.rows)} chats shown (limit {limit})."
        if batch.scan_truncated:
            note += f" Scanning stopped after {batch.scanned} chats; narrow the query."
        elif not batch.rows and batch.scanned:
            note += f" Nothing matched among the {batch.scanned} chats scanned."
        else:
            note += " Narrow the query if the chat is missing."
        return {"items": batch.rows, "returned": len(batch.rows), "note": note}

    async def resolve(self, ref: str) -> dict:
        return await self.gateway.resolve(parse_chat_ref(ref))
```

Move `_read_messages`, `_search_messages`, `_download_media`, `_forward_envelope`, `_backward_envelope`, `_remaining`, and `_moment` into this module as methods or private helpers without changing their algorithms.

- [x] **Step 4: Turn `server.py` into a transport adapter**

Construct one application instance inside `build_server` and delegate every tool body to it through the existing `_guarded` error conversion. Keep the existing MCP-only `sent_so_far` counter until Task 4.

```python
application = TelegramApplication(config, gateway)

@server.tool(description="Your chats, optionally filtered by title.", annotations=_LOOKING)
async def list_dialogs(query: str | None = None, limit: Limit = DEFAULT_ITEMS) -> dict:
    return await _guarded(application.dialogs(query=query, limit=limit))
```

- [x] **Step 5: Run application and MCP parity tests**

Run: `./.venv/bin/python -m pytest tests/test_application.py tests/test_server_tools.py tests/test_history_filters.py tests/test_paging.py tests/test_jsonl.py -q`

Expected: all pass; existing MCP assertions remain unchanged.

- [x] **Step 6: Execute the task review gate**

Apply the global review gate to the Task 1 diff and acceptance criteria mapped to S1. Include the before/after MCP tool list and one concrete history envelope in the evidence.

- [x] **Step 7: Commit Task 1**

```bash
git add src/telegram_plugin/application.py src/telegram_plugin/server.py tests/test_application.py tests/test_server_tools.py
git commit -m "Выделяем application layer Telegram"
```

---

### Task 2: Add the read CLI, exact-message/thread primitives, and lifecycle contract

**Files:**

- Create: `src/telegram_plugin/cli.py`
- Create: `bin/telegram`
- Create: `tests/test_cli.py`
- Create: `tests/test_gateway_messages.py`
- Modify: `src/telegram_plugin/application.py`
- Modify: `src/telegram_plugin/client.py`
- Modify: `src/telegram_plugin/errors.py`
- Modify: `tests/fakes.py`
- Modify: `tests/test_launcher.py`
- Modify: `scripts/setup.sh`

**Interfaces:**

- Consumes: `TelegramApplication` from Task 1 and the existing config loader/state bootstrap.
- Produces: `build_parser()`, `dispatch(arguments, application)`, async `_run(arguments, environment, gateway_factory)`, and `main(argv=None, environment=None, gateway_factory=TelethonGateway) -> int`.
- Extends `TelegramGateway` with `message(ref, message_id) -> dict` and `thread(ref, root_message_id, limit) -> Batch`.

- [x] **Step 1: Write failing gateway tests for exact messages and threads**

Use contract-faithful fakes: exact lookup returns one rendered message or raises `MessageNotFound`; thread returns replies in ascending id order and excludes unrelated replies.

```python
@pytest.mark.asyncio
async def test_fake_thread_returns_only_root_replies(rows):
    gateway = FakeGateway(rows=rows)
    batch = await gateway.thread(parse_chat_ref("@alpha"), root_message_id=10, limit=50)
    assert [row["id"] for row in batch.rows] == [11, 14]
    assert all(row["reply_to"]["thread_id"] == 10 for row in batch.rows)
```

- [x] **Step 2: Run gateway tests and confirm missing-interface failures**

Run: `./.venv/bin/python -m pytest tests/test_gateway_messages.py -q`

Expected: failures report missing `message` and `thread` methods.

- [x] **Step 3: Implement exact-message and thread gateway operations**

Add the two protocol methods, implement exact retrieval with Telethon's id lookup, render through the existing `_render`, and implement thread iteration using `reply_to=root_message_id`. Raise a dedicated `MessageNotFound(message_id)` rather than returning an empty object.

```python
async def message(self, ref: ChatRef, message_id: int) -> dict:
    async with self._session() as client:
        entity = await self._entity(ref)
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise MessageNotFound(message_id)
        username, internal = _link_identity(entity)
        return self._render(message, username, internal)
```

- [x] **Step 4: Write failing CLI contract tests**

Test every read command, compact UTF-8 JSON plus newline, no stderr on normal success, JSON error on stdout with exit `1`, argparse usage on stderr with exit `2`, and gateway closure for success and failure.

```python
def test_domain_error_is_one_json_object_on_stdout(capsys, fake_gateway):
    code = main(["message", "@alpha", "404"], gateway_factory=lambda _: fake_gateway)
    captured = capsys.readouterr()
    assert code == 1
    assert json.loads(captured.out)["error"]["code"] == "message_not_found"
    assert captured.err == ""
```

- [x] **Step 5: Run CLI tests and verify the red state**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`

Expected: collection fails because `telegram_plugin.cli` does not exist.

- [x] **Step 6: Implement parser, dispatch, error serialization, and cleanup**

Create subparsers for `whoami`, `dialogs`, `resolve`, `message`, `thread`, `read`, `search`, and `download`. Convert ISO arguments in the application layer. Catch argparse separately from domain/runtime exceptions, serialize errors with stable codes, and always close the gateway.

```python
async def _run(arguments, environment, gateway_factory=TelethonGateway) -> dict:
    config = load_config_from_environment(environment)
    ensure_state_dir(config)
    gateway = gateway_factory(config)
    try:
        return await dispatch(arguments, TelegramApplication(config, gateway))
    finally:
        await gateway.close()
```

- [x] **Step 7: Add the universal launcher and setup parity**

Copy the proven state-directory venv, install lock, dependency stamp, interpreter override, and stderr-only bootstrap behavior from `bin/telegram-mcp`. Replace the final module with `telegram_plugin.cli`; do not change the MCP launcher yet. Update `scripts/setup.sh` to prewarm through `bin/telegram --help` without touching Telegram.

- [x] **Step 8: Run the focused Task 2 suite**

Run: `./.venv/bin/python -m pytest tests/test_cli.py tests/test_gateway_messages.py tests/test_launcher.py tests/test_application.py tests/test_session_lifecycle.py tests/test_session_lock.py -q`

Expected: all pass, including explicit close assertions on every exit path.

- [x] **Step 9: Execute the task review gate**

Apply the global gate to Task 2. Add a launcher positive control that deliberately points at a nonexistent module and proves the launcher test fails before restoring the correct module.

- [x] **Step 10: Commit Task 2**

```bash
git add bin/telegram scripts/setup.sh src/telegram_plugin/application.py src/telegram_plugin/cli.py src/telegram_plugin/client.py src/telegram_plugin/errors.py tests/fakes.py tests/test_application.py tests/test_cli.py tests/test_gateway_messages.py tests/test_launcher.py
git commit -m "Добавляем Telegram CLI для чтения"
```

---

### Task 3: Add deterministic chat discovery and migrate read/login skills

**Files:**

- Create: `src/telegram_plugin/matching.py`
- Create: `tests/test_matching.py`
- Modify: `src/telegram_plugin/application.py`
- Modify: `src/telegram_plugin/cli.py`
- Modify: `skills/read/SKILL.md`
- Modify: `skills/login/SKILL.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_skills.py`

**Interfaces:**

- Consumes: dialog rows with `id`, `title`, `type`, `username`, and `unread`.
- Produces: `rank_dialogs(dialogs: Iterable[dict], query: str, limit: int) -> list[dict]` and `TelegramApplication.find_chat(query, limit=10) -> dict`.
- Returns each candidate with original dialog fields plus integer `score` and string `matched_by`.

- [x] **Step 1: Write failing normalization and ranking tests**

Cover Unicode normalization, case folding, leading `@`, exact username, title prefix, substring, unordered tokens, one typo, deterministic ties, minimum score, and output limit.

```python
def test_username_fragment_beats_title_typo():
    ranked = rank_dialogs(
        [
            {"id": 1, "title": "Release Chat", "username": "mobile_release"},
            {"id": 2, "title": "Mobile Relese", "username": None},
        ],
        "@mobile_rel",
        limit=5,
    )
    assert ranked[0]["id"] == 1
    assert ranked[0]["matched_by"] == "username_prefix"
```

- [x] **Step 2: Run matching tests and verify missing-module failure**

Run: `./.venv/bin/python -m pytest tests/test_matching.py -q`

Expected: collection fails because `telegram_plugin.matching` does not exist.

- [x] **Step 3: Implement deterministic ranking without a new dependency**

Normalize with `unicodedata.normalize("NFKC", value).casefold()`, strip an initial `@`, convert punctuation to spaces, and collapse whitespace. Score the best of title and username by category: exact `100`, prefix `90`, substring `80`, all tokens `70`, and `SequenceMatcher` typo similarity from `40` through `69` only when ratio is at least `0.60`. Sort by descending score, then casefolded title, then numeric id.

- [x] **Step 4: Add application and CLI `find-chat`**

Fetch at most `DIALOG_SCAN_CAP` dialog rows without a title filter, rank them, and return a bounded envelope with the gateway's scan metadata. `find-chat` accepts a non-empty query and `--limit` in `1..50`.

- [x] **Step 5: Rewrite read and login skills around CLI workflows**

Make the read description explicitly trigger on Telegram, телега, chats, channels, messages, files, remembered people/chat names, and `t.me`. Document exact resolve → find-chat → global-search → sample-read → disambiguate order. Use `"${CLAUDE_PLUGIN_ROOT}/bin/telegram"` for Claude and explain direct absolute launcher use for generic hosts. Remove MCP tool names and instructions. Update login text so a busy session no longer refers to an MCP server.

- [x] **Step 6: Pressure-test skill behavior and add the forbidden-vocabulary guard**

Use `superpowers:writing-skills` with fresh agents to pressure-test at least these prompts: `что
писал Дима в телеге`, `найди канал про релиз`, a partial username, a remembered phrase without a
link, and two ambiguous candidates. Record whether each agent chooses exact resolve, `find-chat`,
global search, bounded read, or disambiguation as the scenario requires. Separately add a static
positive-control guard proving that real skill files contain no `list_dialogs`, `resolve_chat`,
`read_messages`, `search_messages`, `download_media`, or `telegram-mcp`.

- [x] **Step 7: Run the focused Task 3 suite**

Run: `./.venv/bin/python -m pytest tests/test_matching.py tests/test_cli.py tests/test_application.py tests/test_skills.py tests/test_docs.py -q`

Expected: all pass and the candidate order is deterministic across repeated runs.

- [x] **Step 8: Execute the task review gate**

Apply the global gate. For the “no MCP vocabulary in skills” claim, first run the matcher against a fixture containing every forbidden term, then run it against real skills and inspect one concrete skill command.

- [x] **Step 9: Commit Task 3**

```bash
git add src/telegram_plugin/application.py src/telegram_plugin/cli.py src/telegram_plugin/matching.py skills/login/SKILL.md skills/read/SKILL.md tests/test_application.py tests/test_cli.py tests/test_docs.py tests/test_matching.py tests/test_skills.py
git commit -m "Добавляем поиск Telegram-чатов без точной ссылки"
```

---

### Task 4: Add explicit send/reply and remove the fake per-process quota

**Files:**

- Create: `skills/send/SKILL.md`
- Modify: `src/telegram_plugin/application.py`
- Modify: `src/telegram_plugin/cli.py`
- Modify: `src/telegram_plugin/client.py`
- Modify: `src/telegram_plugin/config.py`
- Modify: `src/telegram_plugin/errors.py`
- Modify: `tests/fakes.py`
- Modify: `tests/test_application.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_server_tools.py`
- Modify: `tests/test_skills.py`

**Interfaces:**

- Consumes: `Config.allow_send`, `parse_chat_ref`, output-root confinement, and Telegram message ids.
- Produces: `TelegramApplication.send(chat, text, reply_to=None) -> dict`; gateway `send(ref, text, reply_to=None) -> dict`; CLI mutually exclusive `--text` and `--text-file`; `SendDisabled` error code.

- [ ] **Step 1: Write failing application/CLI tests for side-effect boundaries**

Cover disabled-by-default refusal before gateway access, one send per invocation, reply id forwarding, exact recipient/result fields, mutually exclusive text sources, empty text rejection, and confined text-file reads.

```python
@pytest.mark.asyncio
async def test_send_is_refused_before_gateway_access(config):
    gateway = FakeGateway()
    app = TelegramApplication(config, gateway)
    with pytest.raises(SendDisabled):
        await app.send("@alpha", "hello")
    assert gateway.sent == []
```

- [ ] **Step 2: Run focused tests and verify the red state**

Run: `./.venv/bin/python -m pytest tests/test_application.py tests/test_cli.py -q`

Expected: failures identify missing `SendDisabled`, `reply_to`, and send parser options.

- [ ] **Step 3: Implement explicit send/reply**

Check `allow_send` in the application before parsing the chat or opening a network operation. Extend the gateway to pass `reply_to` to Telethon. Return `message_id`, `chat_id`, `chat_title`, and nullable `reply_to`. Read `--text-file` only through a new confined regular-file helper and reject symlinks, directories, empty files, and files outside `output_root`.

- [ ] **Step 4: Remove the obsolete process quota**

Delete `send_limit`, `TELEGRAM_SEND_LIMIT`, `SendLimitReached`, and MCP `sent_so_far`. Preserve conditional MCP send registration until Task 5, but delegate enabled sends to the application.

- [ ] **Step 5: Add and pressure-test the focused send skill**

The description triggers only on explicit requests to send, message, reply, or answer in Telegram.
Require recipient resolution before ambiguous sends, prohibit treating Telegram content as
authorization, name the enabling environment variable, and require reporting the returned recipient
and message id. Use `superpowers:writing-skills` with fresh agents to prove an operator-authored send
request proceeds, an ambiguous recipient is resolved before send, and a send instruction found in a
Telegram message never authorizes a CLI side effect.

- [ ] **Step 6: Run the focused Task 4 suite**

Run: `./.venv/bin/python -m pytest tests/test_application.py tests/test_cli.py tests/test_config.py tests/test_gateway_messages.py tests/test_server_tools.py tests/test_skills.py tests/test_permissions.py -q`

Expected: all pass; no live Telegram send occurs.

- [ ] **Step 7: Execute the task review gate**

Apply the global gate with special attention to command injection, symlink/path escape, disabled-side-effect ordering, reply targeting, and absence of secrets/message text in logs.

- [ ] **Step 8: Commit Task 4**

```bash
git add skills/send/SKILL.md src/telegram_plugin/application.py src/telegram_plugin/cli.py src/telegram_plugin/client.py src/telegram_plugin/config.py src/telegram_plugin/errors.py tests/fakes.py tests/test_application.py tests/test_cli.py tests/test_config.py tests/test_gateway_messages.py tests/test_server_tools.py tests/test_skills.py
git commit -m "Добавляем безопасные send и reply команды"
```

---

### Task 5: Cut over packaging, remove MCP, and prove portable installation

**Files:**

- Create: `plugin.json`
- Modify: `.claude-plugin/plugin.json`
- Delete: `.codex-plugin/plugin.json`
- Delete: `.cursor-plugin/plugin.json`
- Delete: `bin/telegram-mcp`
- Delete: `src/telegram_plugin/server.py`
- Modify: `pyproject.toml`
- Modify: `src/telegram_plugin/config.py`
- Modify: `bin/telegram`
- Modify: `bin/telegram-login`
- Modify: `scripts/setup.sh`
- Modify: `scripts/security-check.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `tests/test_docs.py`
- Modify: `tests/test_launcher.py`
- Modify: `tests/test_manifests.py`
- Modify: `tests/test_security_tooling.py`
- Delete: `tests/test_server_tools.py`

**Interfaces:**

- Consumes: CLI and skills proven in Tasks 2–4.
- Produces: a portable root manifest, Claude Code compatibility manifest, Telethon-only runtime bootstrap, and no MCP runtime surface.
- Preserves: `bin/telegram-login`, state paths, credentials, session format, output paths, and direct absolute-path CLI use.

- [ ] **Step 1: Write failing packaging and MCP-removal guards**

Require a root manifest with the Agent Plugins schema, portable metadata, and automatic root `skills/` discovery. Require the Claude manifest to name the same plugin/version without `mcpServers`. Add a repository guard that rejects tracked `mcp`, `mcpServers`, `telegram-mcp`, and old MCP tool-call names outside archived design history.

```python
def test_portable_manifest_is_canonical():
    manifest = json.loads((REPO / "plugin.json").read_text())
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "telegram"
    assert "skills" not in manifest
    assert "mcp" not in json.dumps(manifest).lower()
```

- [ ] **Step 2: Prove the guards fail on current MCP files**

Run: `./.venv/bin/python -m pytest tests/test_manifests.py tests/test_security_tooling.py -q`

Expected: failures enumerate the current MCP launcher, server, dependency, and manifest entries.

- [ ] **Step 3: Add canonical portable and Claude manifests**

Create root `plugin.json` with `$schema`, `name`, version `0.6.0`, `description`, and
`extensions.com.openai.interface` metadata. Set `pyproject.toml` and the Claude manifest to the same
version. Keep paths relative to the root. Update `.claude-plugin/plugin.json` to advertise skills
only. Remove unverified Codex/Cursor compatibility manifests rather than claim support through
stale formats.

- [ ] **Step 4: Remove MCP runtime code and dependency**

Delete the MCP server and launcher, remove `mcp>=2,<3`, remove MCP-only configuration/constants/tests, and change the shared dependency bootstrap to Telethon only. Update login's busy-session hint so it says another Telegram command or agent holds the session.

- [ ] **Step 5: Rewrite user and architecture documentation**

Document installation for Claude Code, Codex/OpenAI plugins, direct CLI use by generic local agents, setup, login, all commands, JSON/error contract, natural discovery, pagination/export, safe sending, concurrency, limitations, and the unsupported web-only case. Replace `docs/design.md` with the implemented CLI-first architecture and retain durable security/domain rationale without volatile measurements.

- [ ] **Step 6: Update CI and security tooling**

Make CI validate both manifests, executable launchers, tests, Ruff, Bandit, dependency audit, shellcheck, secret history, personal paths, and the no-MCP guard. Ensure `scripts/security-check.sh` checks `bin/telegram`, `bin/telegram-login`, and `scripts/setup.sh`.

- [ ] **Step 7: Run focused packaging tests**

Run: `./.venv/bin/python -m pytest tests/test_manifests.py tests/test_launcher.py tests/test_docs.py tests/test_skills.py tests/test_security_tooling.py -q`

Expected: all pass and the no-MCP matcher reports zero real repository matches outside explicitly excluded historical spec text.

- [ ] **Step 8: Run a clean bootstrap test**

Use a newly created temporary state directory and execute `TELEGRAM_STATE_DIR=<temp> ./bin/telegram --help`. Verify exit `0`, usage on stdout, dependency installation only on stderr, a private state directory, and a dependency stamp that excludes MCP. Run the command a second time and verify it does not reinstall dependencies.

- [ ] **Step 9: Run live read-only smoke verification**

With the operator's existing authorized session, run `whoami`, `find-chat` with a partial remembered name, `resolve`, `read --limit 3`, `search --limit 3`, `message`, `thread`, and one attachment download into `output_root`. Record redacted command status and output shapes, never message content or credentials. Skip only the attachment step if no candidate message with media exists and record that as manual NO EVIDENCE rather than PASS.

- [ ] **Step 10: Execute the final task review gate**

Give the complete `fa99595..HEAD` diff, all slice verdicts, clean-bootstrap evidence, no-MCP positive control, and live smoke evidence to a fresh reviewer. Fix and re-run until no actionable finding remains, then run the final acceptance gate.

- [ ] **Step 11: Commit Task 5**

```bash
git add -A
git commit -m "Переводим Telegram plugin на portable CLI"
```

## Completion state

The branch is complete only when Tasks 1–5 are separately committed, every mapped acceptance criterion is PASS, all review findings are closed, the full suite and security checks pass from the final tree, clean bootstrap succeeds twice, live read-only smoke evidence exists, and the final tree contains no active MCP runtime or advertised unsupported host integration.
