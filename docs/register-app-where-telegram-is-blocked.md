# Register an application where Telegram is blocked

A source checkout needs its own `api_id` and `api_hash` from
https://my.telegram.org/apps. In a country that blocks Telegram, the form there
tends to answer with a bare `ERROR` or `undefined` alert and create nothing.

What worked: open the site **without a VPN**, from an IP address in the country
of your phone number, by pinning a Telegram address that the block misses.

```bash
echo "149.154.167.220 my.telegram.org" | sudo tee -a /etc/hosts
```

Then turn the VPN off, open https://my.telegram.org/apps in a private window and
create the application. The steps below cover each part.

Telegram does not explain the alert. This guide records one working path, tested
in November 2026; it is not a statement of Telegram's rules.

## Why the form fails

Two things get in the way at once:

- **With a VPN** the site opens, but the request comes from another country than
  your phone number, and the form rejects it with the bare alert.
- **Without a VPN** the address that DNS returns for `my.telegram.org` is blocked,
  so the site does not open at all.

Some of Telegram's addresses stay reachable. Pinning one in `/etc/hosts` lets you
reach the site from your real IP address.

## Check that the pinned address works

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

1. Add the line to `/etc/hosts` and flush the DNS cache. On macOS:

   ```bash
   echo "149.154.167.220 my.telegram.org" | sudo tee -a /etc/hosts
   sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
   ```

   Linux reads `/etc/hosts` on the next lookup; no flush is needed.

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

Remove the line from `/etc/hosts`; only the registration needs it.

```bash
sudo sed -i '' '/my\.telegram\.org/d' /etc/hosts   # macOS
sudo sed -i '/my\.telegram\.org/d' /etc/hosts      # Linux
```

Turn the VPN back on before you log in with the plugin. The plugin connects to
Telegram directly and has no proxy setting of its own, so in a country that
blocks Telegram it works only while a system-wide VPN is on.

## If your machine has to stay on the VPN

Do the same on any other machine that sits in the country of your phone number
without a VPN, for example a home computer over remote desktop. The application
is bound to your Telegram account, not to the machine that created it.

## What not to do

Do not take the `api_id` and `api_hash` of another client, such as an official
Telegram app. See [authentication and local trust](auth-security.md) for why the
plugin does not borrow credentials.
