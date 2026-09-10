---
name: login
description: Authorise this plugin's Telegram session, check whether it is authorised, or explain why a tool says it is not. Use when Telegram tools report "Session is not authorised", when the operator asks to log in, set up or connect Telegram, or asks which account is connected.
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# Logging in

Drive this yourself — do not hand the operator a script to run unless the last step
below says you must. Nothing here asks anyone to paste a credential into the
conversation, and nothing here needs a code typed at a prompt.

`LOGIN` below means `${CLAUDE_PLUGIN_ROOT}/bin/telegram-login`. If that path did not
expand, find `bin/telegram-login` two directories above this skill file.

## 1. Look before doing anything

```bash
LOGIN --status
```

It prints JSON and never changes anything. Act on `credentials`, `authorized` and
`session_in_use`:

- `"credentials": "missing"` → go to step 2.
- `"authorized": true` → say which account is connected (`account.name`, `account.id`)
  and stop. There is nothing to do.
- `"session_in_use": true` → a client already holds this session, almost certainly
  this plugin's own MCP server. That means a session exists. Say so; a fresh login
  would need that client stopped first.
- otherwise → go to step 3.

## 2. Credentials, if they are missing

The API id and hash come from https://my.telegram.org. **Do not ask the operator to
paste them into the conversation, and do not read them back to them.** Tell them the
file to put them in — the `state_dir` from the status output, in `.env`:

```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

`.env.example` in the plugin root is a template. Then run step 1 again.

## 3. Log in by confirming a link

This is the whole point of doing it here: no code is typed, so it completes without
leaving the session. Start it detached, because it waits for the link to be confirmed:

```bash
nohup LOGIN --qr --timeout 60 >/dev/null 2>&1 &
```

Then read the link from the status file — `auth-status.json` in the `state_dir`:

```bash
sleep 2 && cat <state_dir>/auth-status.json
```

Give the operator the `url` verbatim and tell them what to do with it: open it on a
device already signed in to Telegram, or scan it as a QR code from
**Settings → Devices → Link Desktop Device**.

Then poll the same file every few seconds until `state` changes:

- `authorized` → report `account.name` and `account.id`. Done; tools work in the next
  session.
- `expired` → the link was never confirmed. Offer to start again from step 3.
- `needs_password` → step 4.

## 4. Two-factor accounts

A confirmed link is not enough when the account has a two-factor password, and a
password must not travel through a tool call. This is the one case where the operator
finishes in their own terminal:

```
<plugin root>/bin/telegram-login
```

It asks for the phone number, the code Telegram sends, and the password with hidden
input. Say plainly why the handoff is happening rather than just printing a command.

## What never happens here

- The session is this plugin's own. **Never copy a `.session` file from another
  application:** Telegram revokes an auth key used by two clients at once, and both
  stop working.
- No credential, code or password is ever requested in the conversation or passed as
  a command-line argument.
- Values from `.env` are never printed back, not even partially.
