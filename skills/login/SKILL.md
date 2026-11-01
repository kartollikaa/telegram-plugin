---
name: login
description: Use when Telegram reports that its session is not authorised, when the operator asks to log in, set up, connect, or check Telegram, or when they ask which account is connected.
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# Logging in

Drive this yourself: check the state, give the operator one command, wait for it to finish,
confirm. The launcher is `${CLAUDE_PLUGIN_ROOT}/bin/telegram-login` on Claude; on Codex or
another local agent, use its absolute path in the installed plugin.

**Sandbox first.** Every Telegram command writes to the plugin's state directory
(`~/.local/state/telegram-plugin` by default) and talks to Telegram. Inside an agent sandbox
that is refused: `--status` then answers `"authorized": "unknown"` with a hint that names the
sandbox. Rerun it outside the sandbox rather than reporting "unknown".

## 1. Check current state

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status
```

The JSON is read-only:

- `"credentials": "missing"` → step 2.
- `"authorized": true` → report `account.name` and `account.id`, then stop.
- `"session_in_use": true` → another local process holds the session. It must finish before
  a fresh login; do not start another client.
- `"authorized": false` → step 3.

## 2. Configure credentials privately

The API id and hash come from https://my.telegram.org. Do not ask the operator to paste them
into the conversation and do not read them back. Tell them to put these keys in `.env` under
the `state_dir` from the status output:

```text
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Use the plugin's `.env.example` as the template, then repeat step 1.

## 3. Hand over one command

The login is interactive, so the operator runs it, not you: run through your own tool it
stops at once with "needs a terminal". Resolve the launcher's absolute path first
(`echo "${CLAUDE_PLUGIN_ROOT}"`): the operator's shell does not have that variable. Then post
the literal path as a fenced `bash` block. In bb chat that block gets a **Run** button that
opens a real terminal under it; in a plain CLI the operator pastes it into their own terminal.

```bash
'/absolute/path/to/plugin/bin/telegram-login'
```

Say in two lines what happens there: a QR code to scan from the phone's Telegram
(**Settings → Devices → Link Desktop Device**), which refreshes itself; or press `p` for phone
number and login code. A two-step password, if the account has one, is asked in the same
terminal with hidden input. Nothing else: no links, no menus, no other options.

## 4. Wait for the outcome, then confirm

Right after posting the block, wait for the login to finish:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --wait
```

It reads `auth-status.json` in the state directory and returns within 8 minutes. On Claude
Code give the call a 600000 ms timeout, or run it in the background. It only reads, so it works
inside the sandbox. It prints the outcome:

- `authorized` → run `--status` and report the account name and id from it.
- `cancelled`, `failed` or `expired` → relay the `hint` in one line and offer the same command
  again.
- `timeout` → ask whether the operator ran it; when they say they are done, run `--status`.

In bb chat the terminal also reports back as "Ran … in the inline terminal — exit N". Exit `0`
means logged in; anything else matches a `--wait` outcome above. Confirm with `--status`
either way.

Never copy a `.session` file from another application. No credential, code, or password is
accepted in conversation or as a command argument: a secret must not travel through a tool call,
so it is typed only into the operator's terminal. Values from `.env` are never printed back.
