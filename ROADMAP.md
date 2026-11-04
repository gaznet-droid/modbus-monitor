# Roadmap

Planned features and ideas. Updated from Claude Code sessions and from planning chats in the Claude app.
Active branch: raspberry-pi (production runs on ModPi). main holds the old FastAPI app only.

## Blockers
- Meter reachability depends on Dad's dynamic public IP and a router port-forward; any IP change stops readings until it's updated.
- Tuya cloud trial ends 23 November; local Juwai access is still unproven.
- Dad's Wi-Fi profile on the Pi is untested; the Pi has no Ethernet, so a wrong password needs a site visit.

## Next
- Shut the Pi down cleanly, check Dad's Wi-Fi details against his router, and move ModPi to Dad's house.
- Job 1: find the meter on Dad's LAN, switch the Pi to the local address, then remove the port-forward and add a DHCP reservation.
- Job 2: test local reads of the two Juwai meters with the saved local keys; if they work, replace Tuya cloud polling before 23 November.

## Later
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
