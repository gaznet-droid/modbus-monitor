# Home page plan (floor plan with eWeLink overlays)

Requested by Dad on 4 November 2026, planned in the Claude app for Claude Code to build.
Status: steps 1 to 3 live on 4 November 2026 (plan drawing, collection, overlays). Step 4 (history) not started.

## What Dad wants
A new tab called **Home** (`#home`) showing a floor plan of his bungalow, redrawn in the
site's own style, with his eWeLink door sensors, switches and room temperatures placed
on the plan where they are in the house. The loft temperature is shown on the page but
off the plan, because the loft isn't on the floor plan.

## Decisions already made
- Redraw the plan as inline SVG in the site's dark theme (`--bg-base`, `--card-bg`,
  `--border`, `--text-main`, `--text-muted`). Do not use or commit the estate-agent image.
- Keep the shape, room positions, door positions and window positions the same as the
  original plan (layout below).
- Room names on the new plan (Gareth's working assumptions, 4 November):
  - the old **Bedroom 2** (top right) is now the **Kitchen**;
  - the old **Kitchen** (bottom right) is now the **Guest Bedroom**;
  - the small porch off the Guest Bedroom is the **Front Porch**;
  - the porch off the Hall is just the **Porch**.
- Leave out all fittings and units: wardrobes, kitchen units, hob, sink, toilet, basin,
  shower tray. Objects can be added later. Room dimensions are off by default.
- The page is read-only, like the rest of the site. Switches show their state only;
  no toggling.
- Never show invented readings. Unlinked or stale devices show an honest state.

## Floor plan layout
Coordinates are approximate, in the original image's pixels (about 105 px per metre),
x to the right, y downwards. The conservatory is at the top (rear of the house).

| Room | Box (x1–x2, y1–y2) | Windows | Doors |
|---|---|---|---|
| Conservatory | 150–675, 150–415 | Top wall either side of the double doors; full left side; full right side | Double French doors, centre of top wall, opening outwards (external) |
| Bedroom 1 | 100–415, 435–850 | — | Wide glazed/sliding opening on top wall into conservatory (x 200–380); door bottom-right into Hall |
| Kitchen (was Bedroom 2) | 425–745, 435–745 | Top wall into conservatory (x 470–630) | Door bottom-left into Hall |
| Hall | 415–745, 755–955 (L-shaped, narrower on the left) | — | To Bedroom 1, Kitchen, Lounge (left), Shower Room (bottom), Porch (right) |
| Porch (off the Hall) | 755–855, 755–970 | Top wall and right wall | Into Hall; external door on the bottom wall |
| Lounge | 100–490, 860–1385 | Bottom wall (x 170–380) | From Hall (top right); to Guest Bedroom (right wall, low down) |
| Cupboards | 495–570, 960–1185 | — | Two small stores and the chimney breast; draw as plain blocks |
| Shower Room | 580–745, 965–1185 | Right wall | From Hall (top wall) |
| Guest Bedroom (was Kitchen) | 495–745, 1195–1425, plus projection 520–765, 1425–1510 | Projection: bottom wall and both sides | From Lounge (left wall); to Front Porch (right wall) |
| Front Porch (off the Guest Bedroom) | 755–885, 1275–1415 | Top wall and right wall | Into Guest Bedroom; external door on the bottom wall |

Draw doors as door swings (arc plus leaf) and windows as the thin double-line style the
site already suits. Room names in `--text-muted`, small caps or similar.

## Overlays
- **Temperature (and humidity if the device reports it):** a chip in the room, e.g.
  "19.4 °C". Muted when stale.
- **Door sensors:** a marker on the matching door. Open and closed must differ by more
  than colour alone (icon or label as well). Show time since last change if available.
- **Switches:** an icon in the room with on/off state and power if reported.
- **Off-plan sensors:** a row of cards below the plan (wrapping on mobile) for Loft,
  Outside, Freezer and the Garage door.
- Tapping or hovering an overlay shows the device name, last update and, later, history.
- Desktop and mobile: the SVG scales to width; chips stay readable on a phone.

## eWeLink inventory (read-only scan, 4 November 12:50)
29 devices on Dad's account (the scan asks for up to 30, so check for more if one is
added). Device ids stay in the Pi's private config, not here. Placement below follows
Gareth's working assumptions of 4 November; Dad hasn't confirmed them yet, so keep the
mapping easy to change. eWeLink holds no room information for the switches and plugs
(their room is unset; the temperature and door sensors are only grouped by type).

| eWeLink name | Kind (uiid) | Suggested place |
|---|---|---|
| Lounge Temperature | temp + humidity (1770) | Lounge |
| Hall Temperature | temp + humidity (1770) | Hall |
| Kitchen Temperature | temp + humidity, comfort (7052) | Kitchen (top right) |
| Bathroom Temperature | temp + humidity (1770) | Shower Room |
| Bedroom Temperature | temp + humidity (1770) | Bedroom 1 |
| Guest Bedroom Temperature | temp + humidity (1770) | Guest Bedroom (bottom right) |
| Conservatory Temperature | temp + humidity (1770) | Conservatory |
| Front door porch Temperature | temp + humidity (1770) | Front Porch (off the Guest Bedroom) |
| Loft Temperature | temp + humidity, comfort (7052) | Card below the plan |
| Outside Temperature | temp + humidity (1770) | Card below the plan |
| Freezer Temperature | temp + humidity (1770) | Card below the plan |
| Front door | door sensor, `lock` field (7003) | Front Porch external door (assumed) |
| Front Porch door | door sensor (3026) | Front Porch to Guest Bedroom door (assumed) |
| Back Door | door sensor (3026) | Porch (off the Hall) external door (assumed) |
| Conservatory door | door sensor (3026) | Conservatory French doors |
| Garage door | door sensor (7003) | Card below the plan |
| TV, Socket 1 | switch (77) | Room unknown |
| Socket 4, Reset meter | Zigbee switch (1009) | Room unknown |
| On = <24 v | Zigbee switch (1256) | Room unknown |
| MINI-DIM | dimmer with power (277) | Room unknown |
| Roborock | smart plug with power (7032) | Room unknown |
| ZBBridge-P | Zigbee hub (168) | Not shown |
| 1000W Inverter, Garage inverter, 600w inverter, Charger, Fridge Freezer | already on the eWeLink tab | Not on the plan |

## Data and collection
- **Code limit:** `collect()` in `router/ewelink.py` rejects more than five device ids.
  That has to change before any Home devices can be collected.
- **Budget:** the five-minute cycle already uses about 288 of the 400-a-day cap. New
  devices must go in the same batched `POST /v2/device/thing` request, not extra calls.
  Check the API's maximum batch size; if it's below the total, use a slower cycle for
  temperatures rather than raising the cap.
- eWeLink currently has a fixed list of five devices in `router/ewelink.py` (`DEVICES`)
  with an explicit `devices` mapping in the private config. The new devices need adding
  in the same way: one explicit discovery run, then a fixed mapping. No recurring
  polling of devices that aren't mapped.
- Keep within the existing eWeLink budget and schedule files. Check how many API calls
  each extra device adds per cycle before enabling; prefer a single batched status call
  if the API allows it.
- Room-to-device placement is configuration, not code: a small mapping (device id to
  room and, for door sensors, which door). Keep it in the private config or a
  non-secret JSON next to the page; no device ids or tokens in the public repo.
- Changing collectors means restarting `modpi` on the production Pi. Do the page and
  plan first (page-only, hot-swappable), then the collector change as a separate step
  with a backup of the eWeLink private files.

## Open questions for Gareth and Dad
1. **Privacy (decided 4 November):** the Home tab is a proof of concept and stays public,
   like the rest of the site, because it has no controls. Simple authentication for Dad
   is on the roadmap backlog for later. Don't add a login as part of this build.
2. Dad to confirm Gareth's assumed placements, especially which door each of
   Front door, Front Porch door and Back Door is on.
3. Which room each switch, socket and the dimmer is in, and whether to show them at all.
4. Answered by the scan: the loft sensor is on eWeLink (Loft Temperature).
5. Is history wanted for temperatures (24 h / 7 d / 30 d like the eWeLink tab)?

## Build steps
1. Draw the plan as SVG in a new Home tab, with no devices: rooms, doors, windows, labels.
   Page-only deploy. Get Gareth's and Dad's sign-off on the drawing.
2. Run an explicit eWeLink discovery for the new devices, agree the mapping, extend the
   collector within budget, back up private files, restart `modpi`.
3. Add the overlays and the Loft card, with stale and unlinked states.
4. Optional: per-device history.

## Done when
- The Home tab draws the plan correctly on phone and laptop, matching the original's
  shape, doors and windows, with the two rooms relabelled.
- Every mapped device shows a genuine current reading or an honest stale or offline state.
- Door states are readable without relying on colour.
- eWeLink calls stay within the budget; existing five devices keep working.
- No login is added in this build; the page stays public as a proof of concept.

## Built (4 November 2026)
- Collection: the batch status call accepts at most 10 devices, so when the private Home
  mapping exists the collector makes one list call (GET /v2/device/thing?num=30) per
  five-minute cycle and stores only mapped devices. Request use is unchanged at about 288 a
  day. Without the mapping it falls back to the original batch call.
- Formats: temperature and humidity arrive in hundredths. Door sensors report lock 1 for
  open and 0 for closed (confirmed with the garage door open). POWR3, MINI-DIM and the
  Roborock plug report power, voltage and current in hundredths.
- Mapping: private, on the Pi only, in /var/lib/modpi/private/ewelink.json.home (device
  id, kind, place, display name). It maps 23 devices. Discovery output is in
  ewelink-inventory.json next to it.
- Placement, as decided by Gareth on 4 November (still to confirm with Dad):
  - Door sensors: the plan's assumptions, except that Dad's test on 4 November showed
    the two front sensors were the other way round. "Front Porch door" is the Front
    Porch's outside door, and "Front door" is the Guest Bedroom to Front Porch door.
    Both are confirmed by Dad.
  - The Freezer is shown in the Kitchen and the TV in the Lounge.
  - Outside, Loft and Garage are separate sections below the plan. The Garage section
    holds the garage door plus Socket 1, Socket 4, Reset meter, the 24 V switch,
    MINI-DIM and Roborock.
  - The ZBBridge and the five inverter and fridge plugs aren't shown on Home.
- Public endpoint /api/home/latest returns names, places and readings only, never device ids.
- Navigation tabs are icon boxes (lightning bolt, solar panel, plug, house).
