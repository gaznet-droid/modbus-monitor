# Progress log

Handoff notes from each Claude Code session, newest at the bottom.

- 2026-10-23 to 26: Tuya Juwai meters integrated with a budgeted, read-only collector; generation charts and mobile layout added; read-only eWeLink monitoring added with POWR3 scaling.
- 2026-10-28: Pi deployment prepared for a Pi Zero W; the Zero W proved unsuitable and was replaced with a Pi 3 Model A+.
- 2026-11-03: ModPi built on the Pi 3A+ (hostname modpi, SSH key only, Tailscale for maintenance). Dashboard service, Tuya collector and Cloudflare tunnel installed and tested.
- 2026-11-04 09:12: Production moved to the Pi. Databases copied with SQLite backup; Mac supervisor on Slave stopped and kept as the rollback copy. Do not start Start-Modbus.command on Slave.
- 2026-11-04: Power tab simplified into a single card. Blocker: Modbus meter unreachable since about 01:18, probably a changed public IP at Dad's. Next: fix the endpoint, add Dad's Wi-Fi, then move the Pi to his house.
- 2026-11-04: Session handoff, ROADMAP.md and PROGRESS.md set up on this branch.
