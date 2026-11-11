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

Publisher distributions contain a shared application profile. The owner accepts
credential exposure and application-wide availability risk. Personal credentials
remain an optional complete-pair override for source development. The plugin does
not borrow another client's credentials or send sessions through an authentication
service. A local program cannot conceal a shared hash from the person running it,
including when packaged as a binary. This is the same distribution constraint
faced by native mobile and browser clients; it is not user-account authorization.

The release builder takes the publisher pair from a private file and injects only
the client profile into the archive. Publisher input, account sessions and local
state are excluded even when accidentally tracked beneath a documentation or skill
directory. File extension allowlists do not replace explicit secret/state exclusions.
The generated profile is ignored by Git and must not be added
to source or allowlisted as a harmless fixture. Source checkout installation does
not have a shared profile; end users install a publisher distribution instead.

References: [Telegram application registration](https://core.telegram.org/api/obtaining_api_id),
[API terms](https://core.telegram.org/api/terms),
[Telethon sign-in](https://docs.telethon.dev/en/stable/basic/signing-in.html).

## Model-facing output boundary

Commands provide account operations, not configuration inspection or arbitrary
code execution. Credential values are excluded from configuration representations
and redacted from structured results, exports, login output, status files, errors
and library logging, including formatted exception text. Registration lasts for
one CLI invocation, covers publisher and override credentials, and is restored
after the invocation. Status reports credential presence/source only. No debug
command may expose configuration or environment contents.
API IDs are redacted as both integer values and text next to letters. Credential
hashes are redacted before IDs so an overlapping ID cannot reveal pieces of a hash.
Terminal text is redacted before trusted ANSI controls are added, preserving QR redraws.
Credential overrides select one complete source: environment, otherwise the state `.env`,
otherwise the publisher profile. A missing half must never come from another source.

This boundary does not stop an agent with arbitrary filesystem or shell access
from reading the distributed profile or importing the runtime. Do not claim that
prompt instructions, encoding, compilation or redaction make those keys secret
from such an agent. A strict guarantee would require a separate access boundary
that holds credentials and enforces allowed operations independently of the agent.

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
