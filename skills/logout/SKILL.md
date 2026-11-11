---
name: logout
description: Use when the operator asks to log out of Telegram, disconnect this plugin, or switch the connected Telegram account.
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# Logging out

Run `"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status` yourself first; on another host
use the installed absolute path. Resolve sandbox access or wait for `session_in_use`
before continuing. Never read session files or credentials.

Start this yourself using the host's background/process-session facility:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --browser --logout
```

The plugin automatically opens a private local form, names the connected account and asks
for confirmation before revoking this plugin's session. An empty answer or `n` cancels.
Do not give the operator a command to paste or send them to a terminal. Do not inspect the
form with browser tools or screenshots; it is human-only, as in the login skill.

Observe the original process. If its handle is unavailable, run `--wait` once while it is
running. `logged_out` / exit `0` means success or an already unauthorised session.
`cancelled` means the operator declined/interrupted; `failed` means revocation was not
confirmed. Relay the hint and check `--status` before claiming disconnection; an unknown
status is not proof. Do not delete a session file as a substitute for logout. API credentials,
downloads and other Telegram devices are kept. If switching accounts was requested, continue
with `/telegram:login` only after successful logout. Browser launch failure must be reported,
not presented as an opened form; remote/headless hosts need a human-owned local input surface.
