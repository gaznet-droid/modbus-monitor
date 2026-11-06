# Roadmap

Planned features and ideas. Updated from Claude Code sessions and from planning chats in the Claude app.
Single branch: main (production runs on ModPi). Everything else was merged in on 6 Nov.

## Blockers
- Meter reachability depends on Dad's dynamic public IP and a router port-forward; any IP change stops readings until it's updated.
- Tuya cloud trial ends 23 November; local Juwai access is still unproven.
- Dad's Wi-Fi profile on the Pi is untested; the Pi has no Ethernet, so a wrong password needs a site visit.

## This week (before the Pi moves to Dad's)
UI tweaks and new integrations while the Pi keeps running at Gareth's. Prefer page-only changes; collector changes need a modpi restart and a backup of the private files first.
- Home tab requested by Dad: his floor plan redrawn as SVG in the site style, with eWeLink door sensors, switches and room temperatures on the plan, and Loft, Outside, Freezer and Garage door as cards below it. Old Bedroom 2 is now the Kitchen, the old Kitchen is the Guest Bedroom, and its porch is the Front Porch; no fittings. eWeLink scan done; placements are Gareth's assumptions until Dad confirms. Read-only. Full plan and open questions in docs/home-page-plan.md. Proof of concept: stays public for now, by decision on 4 Nov. Steps 1 to 3 are live (plan, collection, overlays). Remaining: Dad to confirm the drawing and placements; optional temperature history (step 4); low-battery warnings on the plan (Outside sensor at 39%).

## Next
- Pi deploys itself from GitHub (decided 6 Nov): GitHub main is the single source of truth and the Mac is no longer needed. Claude Code on any device pushes to main; the Pi checks every 5 minutes, runs the tests, deploys, health-checks and rolls back on failure. Page-only changes need no restart. Unit or installer changes are blocked for a manual install. Live commit shown at /version. Live on ModPi since 6 Nov. Remaining: prove it end to end with a push from the phone.
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
- Add a seamless login for Dad once the Home tab and other proofs of concept settle, so door, switch and temperature states aren't public. No Cloudflare Access, and he shouldn't have to keep logging in. Built into the Pi app, allowlisted users only, long-lived sessions renewed on each visit. Order of preference (decided 4 Nov):
  1. Passkeys (Face ID or fingerprint). Enrol each device through a one-time link. Signature checks need ES256: python3-cryptography 43 (Debian package) is already on the Pi, which would be the first non-stdlib runtime dependency, so decide that deliberately.
  2. Remember-this-device links: a one-time link per device sets a long-lived cookie. Also the fallback if passkeys don't work on one of Dad's devices.
  3. Sign in with Google (allowlisted emails), as the last fallback.
  One login covers the whole site, but signed-in state lives in each browser, so Dad signs in once on each phone, tablet or computer he uses and then stays signed in. Passkeys sync across devices on the same Apple or Google account, so after the first setup another device is one Face ID tap; device links need one link per device; Google is one tap where he's already signed in.
  Keep the eWeLink OAuth callback and /health reachable without login. Decide whether it covers the whole site or only the Home tab.
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
