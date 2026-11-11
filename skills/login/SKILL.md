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

Drive this yourself: check state, let the operator choose QR or phone, start the process,
wait for its outcome, and confirm the account. On Claude the launcher is
`${CLAUDE_PLUGIN_ROOT}/bin/telegram-login`; elsewhere use its installed absolute path.

**Sandbox first.** Commands need write access to the plugin's state directory and a
connection to Telegram. The local form also needs permission to bind a loopback port and
open the browser on the operator's machine. If blocked, rerun with the host's permission
mechanism. Do not claim a form opened when the launcher reports failure.

## 1. Check current state

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --status
```

- `credentials: missing`: follow step 2; do not ask an end user for API credentials.
- `authorized: true`: report `account.name` and `account.id`. Mention `/telegram:logout`
  for disconnecting or switching accounts; never log out automatically.
- `session_in_use: true`: let that process finish; do not compete for the session.
- `authorized: false`: continue to step 3.
- `authorized: unknown`: relay the hint and resolve sandbox or connection access first.

## 2. Missing application profile

Publisher distributions contain a shared application profile. An end user only needs
their Telegram login; if the profile is missing, ask them to install the publisher's
distribution. Do not ask them to register an application or supply an API id/hash.

Only an operator explicitly developing from source or choosing personal credentials
configures the API id/hash from https://my.telegram.org privately. They belong in `.env`
under `state_dir`, using `.env.example` as a template:

```text
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Repeat step 1 afterwards. Values from `.env` are never printed back. Never read that file
with a tool; the operator's shell does not have that variable `${CLAUDE_PLUGIN_ROOT}`.
Never inspect the bundled `_application.json`, dump the process environment, or import
configuration to reveal keys. Commands, status, exports and diagnostics redact the
configured application credentials; these controls do not restrict arbitrary shell code.

## 3. Choose and start

Use AskUserQuestion or the host's interactive choice tool for the method only:

- **QR code**: scan with Telegram on the phone, Settings → Devices → Link Desktop Device.
- **Phone number and code**: enter the number, code and optional two-step password privately.

If the operator already chose, use that choice. No choice tool: the local form's menu
(`--browser` without a method) offers both options.

Start the selected method yourself; do not give the operator a command to copy or run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" --browser --method phone
```

For QR use `--browser --method qr`. The process automatically opens a local form. It shows
one QR and replaces it when the token changes; phone, code and 2FA stay in the same form.
No extra terminal, manual command, or second scan is needed for 2FA. Tell the operator to
complete the opened form and return here. Cancel in the form stops the workflow; leaving
it unused times out. Keep the process alive using the host's background/process-session
facility. Do not start another login while this one holds the session.

The form is human-only. Never inspect it using browser automation, DOM snapshots,
screenshots, network tools, or a proxy. Only process output and `auth-status.json` may
be observed. Do not ask the operator to put a phone number, code, password or API credential
in conversation, AskUserQuestion, or tool arguments. A secret must not travel through a
tool call. The form submits directly to the local plugin process, which talks to Telegram.
Never print submitted values back, store them in status, or read a `.session` file.

On a remote/headless host without a browser on the operator's machine, explain the actual
limitation. Do not call a shell pipe a secure interactive terminal. A human-owned terminal
with hidden code/password input remains supported only when the operator explicitly uses it.

### Optional QR in CLI output

If the operator specifically wants QR in CLI and the host displays live raw command output,
run `--method qr --theme dark` (use `--theme light` for a light output surface). This works
without a TTY and flushes a single complete frame immediately. The raw tool output must be
visible to the user; reading an output file into model context alone does not display it.
Never retype, redraw, or copy a QR into an assistant message: model generation can delay
and corrupt it. Do not stack opposite polarities. A refreshed QR replaces the previous token.
If raw output is collapsed, unavailable, or unsuitable, use the automatically opened form.
Do not send the operator to Terminal.app, iTerm, or a manual shell command.

If this CLI-only path returns `needs_password` / exit `4`, start `--browser --resume`
yourself. It asks only for the password against the approved session, without another scan.

## 4. Wait and confirm

Observe the original process until it exits. If its handle is unavailable, use `--wait`
once while it is running, then check `--status`; do not start repeated watchers.
`--wait` ignores stale outcomes from earlier runs. `timeout` means no fresh result arrived:
check status and the original process; do not announce cancellation or success from that alone.

- `authorized`: run `--status` and report the confirmed account name/id only when
  `authorized` is true. Mention `/telegram:logout`.
- `needs_password`: use `--browser --resume`, then observe that process.
- `cancelled`, `expired`, `failed`: relay the hint and offer a retry of the chosen method.
- `logged_out`: the logout workflow completed; use `/telegram:logout` for its details.

Never copy a `.session` file from another application. Validate host input/output mechanics
separately from pseudo-terminal tests before claiming a host workflow works.
