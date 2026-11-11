---
name: login
description: Use when Telegram reports that its session is not authorised, when the operator asks to log in, set up, connect, or check Telegram, or when they ask which account is connected.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

# Logging in

Drive this yourself: check the state, ask which login method the operator wants, run that
workflow, then confirm. The launcher is `${CLAUDE_PLUGIN_ROOT}/bin/telegram-login` on Claude;
on Codex or another local agent, use its absolute path in the installed plugin.

**Sandbox first.** Telegram commands need write access to the plugin's state directory
(`~/.local/state/telegram-plugin` by default) and a connection to Telegram. If `--status`
answers `"authorized": "unknown"` with a sandbox hint, rerun outside the sandbox.

## 1. Check current state

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status
```

- `"credentials": "missing"` → step 2.
- `"authorized": true` → report `account.name` and `account.id`. Mention `/telegram:logout`
  if they want to disconnect or switch accounts; do not log them out automatically.
- `"session_in_use": true` → wait for the other command to finish before starting a login.
- `"authorized": false` → step 3.

## 2. Configure credentials privately

The API id and hash come from https://my.telegram.org. Do not ask the operator to paste them
into the conversation and do not read them back. Tell them to put these keys in `.env` under
the `state_dir` from the status output, using the plugin's `.env.example` as a template:

```text
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Then repeat step 1. Values from `.env` are never printed back.

## 3. Let the operator choose

Use AskUserQuestion, or the host's equivalent interactive choice tool, with two options:

- **QR code here** — show a scannable code in this CLI conversation.
- **Phone number and code** — enter the phone number, login code, and two-step password
  if enabled, with secret input hidden in a terminal.

If the operator already chose in their request, use that choice. Do not start either workflow
before they choose. If the host has no choice tool, ask the same question in text.

### QR code here

Run this yourself as a streaming/background command with unbuffered output and a timeout
long enough for the scan:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --method qr
```

On Claude, set Bash's `run_in_background: true` and Read its output file straight away;
do not block on TaskOutput first. On a host with process sessions, yield after about one
second and read that session's output while it runs.

This mode works without a TTY. **Display the actual QR frame from its output immediately**
in a plain fenced code block, preserving every space and line. Output contains two stacked
copies in opposite polarity: scan the copy with the light background for the current theme.
Say to scan it from the phone's
Telegram under **Settings → Devices → Link Desktop Device**. Do not send the operator to
Terminal.app or iTerm for a QR, and do not claim the QR cannot appear here.

Keep the process running and read its new output. When it prints a refreshed QR frame, show
the newest frame promptly and say it replaces the previous one. Do not wait for process exit
before displaying the code, invent a QR, substitute a link, or restart a running login.

If the host cannot expose output until a process exits, explain that limitation and offer the
interactive launcher in step 4 instead. A streaming host should use the QR workflow above.

- `authorized` / exit `0` → step 5.
- `needs_password` / exit `4` → the QR was approved, but two-step verification remains.
  Use step 4 with `--resume`. It asks only for the hidden password against the same session;
  do not start another QR or ask for the password in conversation.
- `cancelled`, `failed` or `expired` → relay the hint and offer to retry their chosen method.

### Phone number and code

Use step 4 with `--method phone`. Do not ask for the phone number, code or password in chat.

## 4. Hidden terminal input when needed

Resolve the launcher's absolute path first: the operator's shell does not have that variable
`${CLAUDE_PLUGIN_ROOT}`. Give one literal command, with the chosen flags, as a fenced `bash`
block. In bb its **Run** button opens an inline terminal in this session; use it instead of
asking for a separate terminal window. A plain CLI user can run it in their current terminal.

```bash
'/absolute/path/to/plugin/bin/telegram-login' --method phone
```

For an approved QR that needs a password, replace `--method phone` with `--resume`.
Running the launcher without flags also offers a phone/QR menu in that same terminal.

Once the operator starts the command, wait for its result or run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --wait
```

`--wait` reads `auth-status.json`; it can run inside a sandbox. Give it a long tool timeout or
run it in the background. Do not start repeated watchers. `timeout` means no fresh result
arrived: check `--status` and ask whether the command ran before offering a retry.

## 5. Confirm

After either workflow finishes, run `--status` and report the account name and id only when
`authorized` is true. Mention `/telegram:logout` as the way to disconnect. If status is unknown,
say why; do not turn an old `cancelled` or `authorized` result into the current outcome.

Never copy a `.session` file from another application.
A credential, code, or password must not travel through a tool call, conversation, or
command argument: the operator types secrets
only into hidden terminal prompts. A QR frame is the exception needed for the selected QR
workflow; show it only in this operator's conversation.
