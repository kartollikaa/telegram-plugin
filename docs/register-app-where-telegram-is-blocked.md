# Register an application where Telegram is blocked

Use this guide if you need your own `api_id` and `api_hash` for a source
checkout or a personal override. The publisher's archive includes an application
profile for normal sign-in.

## What worked in one test

In November 2026, the author could open https://my.telegram.org/apps through a
VPN, but submitting the form returned `ERROR` or `undefined`. With the VPN off,
the address returned by DNS timed out. Registration succeeded with the VPN off
after pinning a reachable address in `/etc/hosts`. Telegram's alert gave no cause,
and the reason for the earlier failures remains unknown.

## Check an address before pinning it

Run this with the VPN off. `200` means the address serves the real site: `curl`
verifies the `my.telegram.org` certificate, so a wrong host cannot pass.

```bash
curl -s -m 8 -o /dev/null -w "%{http_code}\n" \
  --resolve my.telegram.org:443:149.154.167.220 https://my.telegram.org/auth
```

The connection can be unstable: in one test two requests out of six timed out
and printed `000`. Run the command a few times before deciding the address is dead.

## Find another address if this one stops working

Scan Telegram's `149.154.167.0/24` network for hosts that serve the site. It
takes about two minutes and prints each working address:

```bash
seq 1 254 | xargs -P 32 -I{} curl -s -m 4 --retry 2 --retry-delay 0 \
  -o /dev/null -w "%{http_code} 149.154.167.{}\n" \
  --resolve my.telegram.org:443:149.154.167.{} https://my.telegram.org/auth \
  | grep '^200'
```

```
200 149.154.167.220
```

Use the address it prints in the `/etc/hosts` line. An empty result on a lossy
connection is not final: run the scan again.

## Create the application

1. Open `/etc/hosts` with `sudoedit /etc/hosts`. If it already maps
   `my.telegram.org`, leave that mapping in place and proceed only if it reaches
   the site with the VPN off. Otherwise, add the following line once, using the
   address you verified and keeping the comment so you can find it later.

   ```text
   149.154.167.220 my.telegram.org # telegram-plugin temporary registration
   ```

   On macOS, flush the DNS cache after saving the change.

   ```bash
   sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
   ```

2. Turn the VPN off completely, including browser extensions.
3. Open https://my.telegram.org/apps in a private window and log in with your
   phone number. The code arrives in Telegram itself, so keep a logged-in
   Telegram client at hand.
4. Fill in every field of the form and avoid the word "Telegram" in the title
   and the short name; the form asks for that.
5. Press **Create application** once. If the page hangs, wait and reload it
   instead of submitting again: after two failed attempts in a row the form
   refused further tries for the rest of the day.
6. Put the values into `~/.local/state/telegram-plugin/.env` as the
   [README](../README.md#credentials) describes. Never paste them into a
   conversation with an agent.

## Clean up

If you added a line, reopen `/etc/hosts` with `sudoedit /etc/hosts` and remove
only the line you added with the `# telegram-plugin temporary registration`
comment. Leave any existing mappings, aliases and comments in place. On macOS,
repeat the DNS cache flush after saving.

The plugin connects directly to Telegram's MTProto servers. Before signing in,
make sure those servers are reachable from the environment running the plugin.
A system-wide VPN can provide that access if the local network blocks it.

## If you cannot turn off the VPN on this machine

You can register the application on another machine with direct access to the
registration site. The registration belongs to your Telegram account and can be
used from the machine running the plugin.

## What not to do

Do not take the `api_id` and `api_hash` of another client, such as an official
Telegram app. See [authentication and local trust](auth-security.md) for why the
plugin does not borrow credentials.
