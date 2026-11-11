---
name: logout
description: Use when the operator asks to log out of Telegram, disconnect this plugin, or switch the connected Telegram account.
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# Logging out

Check `"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status` first. On another host use the
installed launcher's absolute path. If a sandbox blocks status, rerun outside it; if another
process holds the session, let it finish before continuing.

Resolve the absolute path; the operator's shell does not have `${CLAUDE_PLUGIN_ROOT}`.
Give the operator one command in a fenced `bash` block. In bb, use its Run button for an inline
terminal; in a plain CLI, use the current terminal:

```bash
'/absolute/path/to/plugin/bin/telegram-login' --logout
```

The command names the connected account and asks for confirmation before revoking this
plugin's Telegram session. An empty answer or `n` cancels. Other Telegram devices, API
credentials, and downloads are kept. Do not delete a session file as a substitute for logout.

Wait for the terminal result or use `--wait` once the operator starts it. `logged_out` / exit
`0` means logout succeeded or no authorised plugin session existed. `cancelled` means the
operator declined or interrupted; `failed` means revocation was not confirmed. Relay the hint
and check `--status` before claiming the account is disconnected. If they asked to switch
accounts, continue with `/telegram:login` after logout succeeds.
