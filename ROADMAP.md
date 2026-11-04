# Roadmap

Planned features and ideas. Updated from Claude Code sessions and from planning chats in the Claude app.
Active branch: raspberry-pi (production runs on ModPi). main holds the old FastAPI app only.

## Blockers
- Meter reachability depends on Dad's dynamic public IP and a router port-forward; any IP change stops readings until it's updated.
- Tuya cloud trial ends 23 November; local Juwai access is still unproven.
- Dad's Wi-Fi profile on the Pi is untested; the Pi has no Ethernet, so a wrong password needs a site visit.

## This week (before the Pi moves to Dad's)
UI tweaks and new integrations while the Pi keeps running at Gareth's. Prefer page-only changes; collector changes need a modpi restart and a backup of the private files first.
- Home tab requested by Dad: his floor plan redrawn as SVG in the site style, with eWeLink door sensors, switches and room temperatures on the plan, and Loft, Outside, Freezer and Garage door as cards below it. Old Bedroom 2 is now the Kitchen, the old Kitchen is the Guest Bedroom, and its porch is the Front Porch; no fittings. eWeLink scan done; placements are Gareth's assumptions until Dad confirms. Read-only. Full plan and open questions in docs/home-page-plan.md. Proof of concept: stays public for now, by decision on 4 Nov. Steps 1 to 3 are live (plan, collection, overlays). Remaining: Dad to confirm the drawing and placements; optional temperature history (step 4); low-battery warnings on the plan (Outside sensor at 39%).

## Next
- Shut the Pi down cleanly, check Dad's Wi-Fi details against his router, and move ModPi to Dad's house.
- Job 1: find the meter on Dad's LAN, switch the Pi to the local address, then remove the port-forward and add a DHCP reservation.
- Job 2: test local reads of the two Juwai meters with the saved local keys; if they work, replace Tuya cloud polling before 23 November.

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
