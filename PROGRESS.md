# Progress log

Handoff notes from each Claude Code session, newest at the bottom.

- 2026-10-23 to 26: Tuya Juwai meters integrated with a budgeted, read-only collector; generation charts and mobile layout added; read-only eWeLink monitoring added with POWR3 scaling.
- 2026-10-28: Pi deployment prepared for a Pi Zero W; the Zero W proved unsuitable and was replaced with a Pi 3 Model A+.
- 2026-11-03: ModPi built on the Pi 3A+ (hostname modpi, SSH key only, Tailscale for maintenance). Dashboard service, Tuya collector and Cloudflare tunnel installed and tested.
- 2026-11-04 09:12: Production moved to the Pi. Databases copied with SQLite backup; Mac supervisor on Slave stopped and kept as the rollback copy. Do not start Start-Modbus.command on Slave.
- 2026-11-04: Power tab simplified into a single card. Blocker: Modbus meter unreachable since about 01:18, probably a changed public IP at Dad's. Next: fix the endpoint, add Dad's Wi-Fi, then move the Pi to his house.
- 2026-11-04: Session handoff, ROADMAP.md and PROGRESS.md set up on this branch.
- 2026-11-04 09:39: Meter readings restored. Dad's site lost internet at 01:18 and came back with a new public IP, which is now set on the Pi. The overnight gap can't be recovered. The meter's reachability still depends on that IP until the Pi reads it locally.
- 2026-11-04: Dad's Wi-Fi has been saved on the Pi since 3 November, but can't be tested until it's on site. Tailscale works from the Mac, and the Pi's key expiry is off, so the Pi can be maintained remotely after the move. The Pi now runs on its own power supply.
- 2026-11-04: Tuya local keys and MAC addresses for both Juwai meters were fetched while the cloud trial still works, and saved on the Pi and on the Mac. Don't re-pair the meters in Smart Life, or the keys change.
- 2026-11-04: Full handoff written in docs/handoff-2026-11-04.md, including the state that lives only on the Pi and the Mac. Next: move the Pi to Dad's, read the meter on his local network, then replace Tuya cloud polling with local reads before 23 November.
