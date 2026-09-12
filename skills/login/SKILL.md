---
name: login
description: Use when Telegram reports that its session is not authorised, when the operator asks to log in, set up, connect, or check Telegram, or when they ask which account is connected.
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# Logging in

Drive this yourself. On Claude, use `${CLAUDE_PLUGIN_ROOT}/bin/telegram-login`. On Codex
or another local agent, substitute that launcher's absolute path in the installed plugin.
Do not hand the operator a script unless the two-factor step explicitly requires their terminal.

## 1. Check current state

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status
```

The JSON status is read-only:

- `"credentials": "missing"` → continue to step 2.
- `"authorized": true` → report `account.name` and `account.id`, then stop.
- `"session_in_use": true` → another local process holds the session. Report that it must
  finish or stop before a fresh login; do not start another client.
- Otherwise continue to step 3.

## 2. Configure credentials privately

The API id and hash come from https://my.telegram.org. Do not ask the operator to paste them
into the conversation and do not read them back. Tell them to put these keys in `.env` under
the `state_dir` from the status output:

```text
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Use the plugin's `.env.example` as the template, then repeat step 1.

## 3. Confirm a login link

Start the QR flow detached:

```bash
nohup "${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --qr --timeout 60 >/dev/null 2>&1 &
```

Read `auth-status.json` from the reported `state_dir`, give its `url` to the operator,
and ask them to open it on a Telegram device or scan it from
**Settings → Devices → Link Desktop Device**. Poll that file until:

- `authorized` → report the account and stop.
- `expired` → offer to restart step 3.
- `needs_password` → continue to step 4.

## 4. Two-factor accounts

A password must not travel through a tool call. Ask the operator to run the
`bin/telegram-login` launcher by absolute path in their own terminal. It collects the phone,
code, and hidden password there.

Never copy a `.session` file from another application. No credential, code, or password is
accepted in conversation or as a command argument. Values from `.env` are never printed back.
