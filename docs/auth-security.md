# Authentication and local trust

## Application credentials and account access

`api_id` and `api_hash` identify the client application. The phone/code or QR flow,
and the two-step password when enabled, authorize a particular user account. One
application can serve multiple accounts. Knowing its API credentials alone does
not authorize access to those accounts.

Someone with a shared API credential pair can use it in another client. Abuse
can lead to restrictions on the application's API access, affecting legitimate
users too. Telegram requires developers to register their own application;
Telethon warns against publishing the API hash and says it cannot be revoked.

The plugin therefore keeps its existing per-installation credential configuration.
It does not bundle the author's personal credentials, borrow another client's
credentials, or send account sessions through an authentication service. A shared
application would require its owner's explicit acceptance of credential exposure
and application-wide availability risk. A local program cannot conceal a shared
hash from the person running it, including when packaged as a binary.

References: [Telegram application registration](https://core.telegram.org/api/obtaining_api_id),
[API terms](https://core.telegram.org/api/terms),
[Telethon sign-in](https://docs.telethon.dev/en/stable/basic/signing-in.html).

## Session and filesystem boundary

The `.session` file contains the account authorization key. A copied session can
permit account access without another code or two-step password. Revoke an exposed
session using Telegram's Devices screen; deleting the local file alone is not a
server-side revocation.

State belongs to the operating-system user running the plugin. Its directory is
private, and session files, SQLite sidecars, credentials and locks must be owned
regular files without symlinks or extra hard links. Checks use opened descriptors
before changing permissions or reading contents. Session names must be basenames.
Read-only probes use the same no-follow, nonblocking file checks: a substituted
FIFO must not hang `--wait` or bypass validation just because no write was requested.
Session files are created privately before Telethon opens them. Status writes use
private temporary files and atomic replacement, including bootstrap failures, so
a substituted status symlink cannot redirect a write or a chmod. Status polling
does not create or chmod configuration; unreadable credentials are reported as
unknown rather than read through a less restrictive fallback.

These protections address unsafe local state and access by other OS users. They
do not sandbox a process already running as the same user. That process can read
the user's files or run a different Telegram client. Keep state outside shared or
synced directories and use trusted parent directories and runtimes.

## Agent access

The private form keeps submitted codes and passwords out of normal tool output.
Its loopback listener checks Host, Origin, a random path, CSRF and prompt revision;
it does not grant CORS access to other websites. These controls do not prevent a
fully privileged local agent from inspecting its host. Do not automate the live
authentication form or expose its listener remotely.

Sending being disabled is a plugin-level restriction. The underlying user session
is not a Telegram-enforced read-only credential. If an agent can run arbitrary
code as the session owner, it can bypass that flag. A stronger boundary requires
an isolated OS identity or a broker that keeps the session inaccessible to the
agent and independently enforces allowed operations.

Reading messages into an LLM conversation shares their contents with the selected
model provider. Local session storage does not make that message processing local.
See [Telethon session storage](https://docs.telethon.dev/en/stable/concepts/sessions.html).
