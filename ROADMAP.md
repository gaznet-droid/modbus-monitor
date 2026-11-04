# Roadmap

Planned features and ideas. Updated from Claude Code sessions and from planning chats in the Claude app.
Active branch: raspberry-pi (production runs on ModPi). main holds the old FastAPI app only.

## Next
- Restore meter readings: the Modbus endpoint has been unreachable since about 01:18 on 4 November, probably a changed public IP at Dad's.
- Add and test Dad's Wi-Fi profile (dad-bthub), then move ModPi to Dad's house.
- Verify the real Modbus endpoint and port from Dad's LAN once on site.

## Later
- Prove local Juwai access (unproven so far; don't promise local collection).
- Decide whether to retire the Slave/Mac copy once the Pi has run cleanly on site.

## Done
- 3 Nov: ModPi (Pi 3A+) built, Tuya collector and Cloudflare tunnel running on it.
- 4 Nov: Production moved from the Mac/Slave to the Pi; Power tab unified into one card.
