# Roadmap

Planned features and ideas. Updated from Claude Code sessions and from planning chats in the Claude app.
Single branch: main (production runs on ModPi). Everything else was merged in on 6 Nov.

## Blockers
- Meter reachability depends on Dad's dynamic public IP and a router port-forward; any IP change stops readings until it's updated.
- Tuya cloud trial ends 23 November; local Juwai access is still unproven.
- Dad's Wi-Fi profile on the Pi is untested; the Pi has no Ethernet, so a wrong password needs a site visit.

## This week (before the Pi moves to Dad's)
UI tweaks and new integrations while the Pi keeps running at Gareth's. Prefer page-only changes; collector changes need a modpi restart and a backup of the private files first.
- Home tab requested by Dad: his floor plan redrawn as SVG in the site style, with eWeLink door sensors, switches and room temperatures on the plan, and Loft, Outside, Freezer and Garage door as cards below it. Old Bedroom 2 is now the Kitchen, the old Kitchen is the Guest Bedroom, and its porch is the Front Porch; no fittings. eWeLink scan done; placements are Gareth's assumptions until Dad confirms. Read-only. Full plan and open questions in docs/home-page-plan.md. Proof of concept: stays public for now, by decision on 4 Nov. Steps 1 to 3 are live (plan, collection, overlays). Step 4 is live: tapping any temperature opens a graph view with room tabs (desktop: graph left, plan right; mobile: graph replaces the plan until closed), and rooms have small icons on the plan. 7 Nov: room labels and icons are white, rooms are filled with the key's colours, temperatures use the same colours, Outside, Loft and the Garage door sit in a small box on the plan top right (the Garage card below keeps the switches), and the Back door sensor sits at the internal Guest Bedroom door. Doors with a sensor are drawn shut on the plan while closed. Next: Gareth's mobile layout changes. Remaining: Dad to confirm the drawing and placements; low-battery warnings on the plan (Outside sensor at 39%).
- eWeLink page (Inverters & fridge) refreshed 6 Nov at Dad's request and live on ModPi: removed the connection/last-checked line and the "tap a device" hint; each device icon now shows status (green and pulsing when on, amber when off, grey when offline) and the old flashing dot is gone; added cumulative energy used over the selected period under the latest power on every card; on web the cards are a list with one graph on the right and the 1000 watt inverter preselected, while mobile is unchanged (tap a device to open its graph). Page-only change. Remaining: Dad to eyeball it.

## Next
- Shut the Pi down cleanly, check Dad's Wi-Fi details against his router, and move ModPi to Dad's house.
- Job 1: find the meter on Dad's LAN, switch the Pi to the local address, then remove the port-forward and add a DHCP reservation. Once the Pi is on Dad's network no port-forward is needed at all: the website goes out through the Cloudflare tunnel and maintenance through Tailscale, both outbound. Removing it also stops the meter's Modbus port being open to the internet.
- Job 2: test local reads of the two Juwai meters with the saved local keys; if they work, replace Tuya cloud polling before 23 November.
- Job 3 (added 4 Nov, Dad wants it): replace the Elfin RS485-to-Wi-Fi box with a Waveshare RS485 CAN HAT (The Pi Hut, WAV-14882, about £12.60) wired straight to the meter. Dad has to reset the Elfin daily because it gets maxed out; the Pi would become the only Modbus master. HAT purchased 4 Nov, due Fri 6 or Sat 7 Nov. Notes:
  - Wiring: the meter's RS485 A/B screw terminals go to the HAT's A/B terminals. No special cable: move the Elfin's existing wires if the Pi sits by the meter, otherwise any twisted pair (Cat5/6 offcut). Add ground if the meter has one. Unplug the Elfin completely, since two masters on one bus clash.
  - Elfin serial settings (from Dad's screenshot of its web page, 4 Nov): EW11 at 192.168.4.38 on Dad's LAN; 9600 baud, 8 data bits, 1 stop bit, parity None (9600 8N1). Dad says the ID is 1 or 0; the collector already reads slave ID 1, and 0 is the Modbus broadcast address (no replies), so use 1. Dad confirmed (4 Nov) nothing else reads through the Elfin, so it can be removed outright. Job 1 can use 192.168.4.38 as the meter's local address until the HAT is fitted.
  - Pi 3A+ setup: `dtoverlay=disable-bt` and serial console off so GPIO14/15 serves `/dev/serial0`; CAN/SPI not needed. The HAT has TVS protection but no isolation.
  - Collector: switch the meter read from Modbus TCP (port 8838) to Modbus RTU on `/dev/serial0`, same slave ID and register 146. Needs a modpi restart and a backup of private files first.
  - Side benefits: removes the public IP and port-forward dependency for the meter (Job 1) and may explain the evening dropouts. Interim option if the HAT is delayed: keep one persistent TCP connection to the Elfin instead of reconnecting each poll.
  - Pi must have Wi-Fi coverage at the meter's location.

## Later
- Login for the whole site (plan agreed 8 Nov, replaces the 4 Nov ordering). Built into the Pi app; no Cloudflare Access; long-lived sessions renewed on each visit, so each device signs in once and stays signed in.
  - Users: Gareth and Dad only. Their two email addresses are the allowlist, kept in the Pi's private folder, never in the public repo. No public sign-up.
  - Sign in two ways: Google (allowlisted emails only), and passkeys (Face ID or Touch ID on Apple, fingerprint or face unlock on Android) for everyday use.
  - An "Admin" link in the site footer opens the sign-in page. Once signed in, the admin page creates one-time, short-lived sign-up links to add a passkey on a new device, and lists passkeys and signed-in devices so either user can remove one. Only a signed-in user can create links.
  - First use: Gareth signs in with Google, then sends Dad a sign-up link, or Dad signs in with Google and adds his own passkey. No SSH needed.
  - Decided against Claude creating links over SSH: links would end up in chat history.
  - Whole site sits behind the login. Must stay reachable without it: the sign-in pages and Google callback, the eWeLink OAuth callback, /health, /version and the GitHub deploy webhook.
  - Needs: a Google Cloud OAuth client (Gareth to set up, with guidance; its secret only on the Pi), and python3-cryptography (already on the Pi as a Debian package) for passkey signatures, the app's first non-standard-library dependency, accepted.
  - Risk accepted: anyone in Gareth's or Dad's Google account could sign in.
- Investigate the meter dropping out most evenings around 17:45–18:30 (seen 28–31 Oct) while eWeLink stayed online.
- Confirm Dad's broadband: his Wi-Fi is a BT Hub, but the meter's public IP is TalkTalk.
- Optionally make Live Net Power and the Combined Meter Reading follow the time window (average power; meter reading at window end needs meter history).
- Decide whether to retire the Slave/Mac rollback copy once the Pi has run cleanly on site.

## Done
- 3 Nov: ModPi (Pi 3A+) built, Tuya collector and Cloudflare tunnel running on it.
- 3 Nov: Dad's Wi-Fi profile added; Tailscale on Pi and Mac for remote maintenance, key expiry off.
- 4 Nov: Production moved from the Mac/Slave to the Pi; Power tab unified into one card.
- 4 Nov: Meter readings restored after Dad's public IP changed (new address set on the Pi only).
- 4 Nov: Tuya local keys and MACs fetched and backed up on the Pi and the Mac.
- 4 Nov: Full handoff in docs/handoff-2026-11-04.md.
- 4 Nov: Home tab live with floor plan, icon navigation, and 23 eWeLink devices (temperatures, doors, switches) collected in one request per cycle.
- 6 Nov: Main is the only branch; ROADMAP and PROGRESS in sync across chats, Claude Code and GitHub.
- 6 Nov: Home tab temperatures tappable for a 24-hour graph (plan chips, Freezer, Outside and Loft).
- 7 Nov: Home graph view with room tabs and room icons; Home tab full browser width on desktop.
- 6 Nov: Pi deploys itself from GitHub: tested, health-checked, rolled back on failure; live commit at /version. No Mac needed.
- 7 Nov: GitHub push webhook set up with a new secret and proven on its own (live about 3 seconds after a push); the backup check is back to once a day at 03:30.
