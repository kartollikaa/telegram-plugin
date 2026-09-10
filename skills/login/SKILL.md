---
name: login
description: Authorise this plugin's Telegram session, or explain why a tool says the session is not authorised. Use when Telegram tools report "Session is not authorised", or the operator asks how to log in or set up Telegram credentials.
user-invocable: true
allowed-tools:
  - Bash(ls *)
  - Bash(cat *)
  - Read
---

# Logging in

The session belongs to this plugin alone. Never copy a `.session` file from
another project: Telegram revokes an auth key that two clients use at once, and
both would stop working.

## Check the state first

The state directory is `$TELEGRAM_STATE_DIR`, or `~/.local/state/telegram-plugin`
by default. Look for two things:

- `telegram.session` — present means a login has happened before.
- `.env` — must carry `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, obtained from
  https://my.telegram.org. Never print their values back to the operator.

## Then hand the command over

Logging in is interactive: Telegram sends a code and the CLI waits for it to be
typed. That cannot be done for the operator from inside a session, so give them
the command and let them run it in their own terminal:

```
<plugin directory>/bin/telegram-login
```

It asks for the phone number, then the code, then a two-factor password if the
account has one. The password prompt is hidden. Nothing is accepted as a
command-line argument.

Once it prints `Authorised as …`, the tools work in the next session. If it
reports that another process holds the session, the plugin's server is already
running with it — stop that client first.
