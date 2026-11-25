# Better Xiaomi Cloud Map Extractor

[![Latest release](https://img.shields.io/github/v/release/Igloczek/better-xiaomi-cloud-map-extractor)](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/releases)
[![Tests](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A Home Assistant integration that downloads your robot vacuum's map from the Xiaomi cloud and shows
it as an image entity, with the robot's position, path, rooms, zones and obstacles drawn on it. It
works with Xiaomi, Roborock (Mi Home app), Viomi, Roidmi, Dreame and Ijai vacuums.

Part of the **Better** family for Home Assistant vacuums, maintained together and built test-first:

- **Better Xiaomi Cloud Map Extractor** (this repository): the live map image and room data from
  the Xiaomi cloud.
- **[Better Xiaomi Vacuum Map Card](https://github.com/Igloczek/lovelace-better-xiaomi-vacuum-map-card)**:
  the dashboard card for this map. It turns the map into a remote: tap rooms to clean them, draw
  zones, send the robot to a spot. Report problems with the card itself on its own issue tracker.

<img src="https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/images/map_no_rooms.png" width=48%>  <img src="https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/images/map_rooms.png" width=48%>

## Why this fork

The original [Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor) has had no release since
v3.0.0-alpha-24 and has a long backlog of open issues and pull requests. This fork continues the v3
line: setup in the UI, login with a QR code or password (with captcha and two-factor support), and
the map as an `image` entity. Every fix comes with a test, and the plan is in [ROADMAP.md](ROADMAP.md).
The differences from the original are listed in [FORK.md](FORK.md), released changes in
[CHANGELOG.md](CHANGELOG.md).

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Igloczek/better-xiaomi-cloud-map-extractor` with type **Integration**.
3. Search for **Better Xiaomi Cloud Map Extractor**, download it and restart Home Assistant.

### Manual

Download [`better_xiaomi_cloud_map_extractor.zip`](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/releases/latest/download/better_xiaomi_cloud_map_extractor.zip)
and extract it into `config/custom_components/better_xiaomi_cloud_map_extractor`:

```bash
mkdir -p custom_components/better_xiaomi_cloud_map_extractor
cd custom_components/better_xiaomi_cloud_map_extractor
wget https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/releases/latest/download/better_xiaomi_cloud_map_extractor.zip
unzip better_xiaomi_cloud_map_extractor.zip
rm better_xiaomi_cloud_map_extractor.zip
```

Restart Home Assistant afterwards.

## Coming from Xiaomi Cloud Map Extractor

This integration has its own name (`better_xiaomi_cloud_map_extractor`), so it can be installed next
to the original. Your vacuums move over with their settings, Xiaomi login, device and entity ids, so
dashboards, automations and the map card keep working. Keep the original installed until the end:

1. Install this integration as above, next to the original, and restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration → Better Xiaomi Cloud Map Extractor** and
   pick **Import from Xiaomi Cloud Map Extractor**. Choose the vacuum and confirm. Further vacuums
   then show up under **Discovered**; import each the same way.
3. The import disables the vacuum's entry in Xiaomi Cloud Map Extractor (you can enable it again;
   nothing is deleted) and moves its device and entities here. No new login is needed.
4. Settings → Repairs then shows "<vacuum> moved from Xiaomi Cloud Map Extractor" with what is left:
   - if the original was set up from v2 YAML and its `camera:` block is still in your configuration,
     open "Remove the leftover YAML of the original Xiaomi Cloud Map Extractor" and submit it: it
     removes the block (a backup of the file is saved first; no restart needed);
   - delete the disabled Xiaomi Cloud Map Extractor entry (the device and entities stay here);
   - uninstall Xiaomi Cloud Map Extractor in HACS.

The import never changes or deletes the original's configuration: it only disables the entry you
confirm.

Still on v2 (YAML `camera:` configuration)? Change `platform: xiaomi_cloud_map_extractor` to
`platform: better_xiaomi_cloud_map_extractor` and restart.
The vacuum is imported into a configured integration on start, logging in with the username and
password from the YAML. Then remove the YAML block and restart (Repairs says so), and log in again
if Home Assistant asks you to re-authenticate. The v2 documentation stays on the
[`v2` branch](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/tree/v2#readme).

## Setup

1. Go to **Settings → Devices & services → Add integration** and pick
   **Better Xiaomi Cloud Map Extractor**.
2. Log in to your Xiaomi account (the one used by the Xiaomi Home / Mi Home app, not the Roborock
   app): scan the QR code with the Xiaomi Home app, or enter your email and password. Answer a
   captcha or a two-factor code if Xiaomi asks for one.
3. Pick your vacuum if the account has several.
4. Check the vacuum's IP address and token (both filled in from the cloud) and the map format (the
   detected one is marked with `*`).

You get a **Live map** image entity. Further entities (a camera, the robot and charger position,
the room the robot is in, no-go and no-mop areas, a force-update button and an auto-update switch)
are created disabled; enable the ones you need. Colours, drawn elements, sizes, rotation and trim are
in the integration's **Configure** options. For Ijai vacuums (and the Xiaomi models on the Ijai map
format), **Image dimensions → Fit to the explored area** crops the map to the part the vacuum has
explored and enlarges it; it is off by default.

## Supported devices

The map format is detected from the model and marked with `*` in the last setup step (**Used map
API**). A model not listed here may still work with one of the formats; pick it there.

Tested on these vacuums:

- Roborock map format (`roborock.vacuum.*`, `rockrobo.vacuum.*`):
  - `rockrobo.vacuum.v1` (Xiaomi Vacuum Gen 1, Mi Robot Vacuum, SDJQR01RR, SDJQR02RR)
  - `roborock.vacuum.m1s` (Xiaomi Mi Robot 1S)
  - `roborock.vacuum.s4` (Roborock S4)
  - `roborock.vacuum.s5` (Roborock S5)
  - `roborock.vacuum.s5e` (Roborock S5 Max)
  - `rockrobo.vacuum.s6` (Roborock S6)
  - `roborock.vacuum.a08` (Roborock S6 Pure)
  - `roborock.vacuum.a10` (Roborock S6 MaxV)
  - `roborock.vacuum.a15` (Roborock S7)
  - `roborock.vacuum.a19` (Roborock S4 Max)
  - `roborock.vacuum.a27` (Roborock S7 MaxV)
  - `roborock.vacuum.a70` (Roborock S8 Pro Ultra)
- Viomi map format (`viomi.vacuum.*`):
  - `viomi.vacuum.v6` (Viomi Vacuum V2 Pro, Xiaomi Mijia STYJ02YM, Mi Robot Vacuum Mop Pro)
  - `viomi.vacuum.v7` (Mi Robot Vacuum-Mop Pro)
  - `viomi.vacuum.v8` (Mi Robot Vacuum-Mop Pro)
  - `viomi.vacuum.v13` (Viomi V3)
- Roidmi map format (`roidmi.vacuum.*`, `zhimi.vacuum.*`, `chuangmi.vacuum.*`, and
  `viomi.vacuum.v18`, `v23` and `v38`):
  - `roidmi.vacuum.v60` (Roidmi EVE Plus)
  - `viomi.vacuum.v18` (Viomi S9)
  - `viomi.vacuum.v38` (Viomi V5 Pro)
  - `zhimi.vacuum.xa1` (Lydsto R1)
  - `chuangmi.vacuum.hmi707` (IMILAB V1 Vacuum)
- Dreame map format (`dreame.vacuum.*`):
  - `dreame.vacuum.mc1808` (Xiaomi Mi Mop/Xiaomi Mijia 1C)
  - `dreame.vacuum.p2008` (Dreame F9)
  - `dreame.vacuum.p2009` (Dreame D9)
  - `dreame.vacuum.p2028` (Dreame Z10 Pro)
  - `dreame.vacuum.p2029` (Dreame L10 Pro)
  - `dreame.vacuum.p2036` (Trouver LDS Cleaner)
  - `dreame.vacuum.p2041o` (Xiaomi Mop 2 Pro+)
  - `dreame.vacuum.p2140` (Mijia Robot Vacuum-Mop 2C)
  - `dreame.vacuum.p2157` (MOVA L600)
  - `dreame.vacuum.p2259` (Dreame D9 Max)
- Ijai map format (`ijai.vacuum.*`, and `xiaomi.vacuum.b106eu`, `c103` and `d106gl`): Ijai vacuums
  such as the Mi Robot Vacuum-Mop 2 Pro (`ijai.vacuum.v3`) and Xiaomi S10, S12 and S20.
- Xiaomi map format (the other `xiaomi.vacuum.*` models), such as the H50 (`ov43gb`) and the M40S
  (`ov31cn`).

Models that still fail, and what is needed to support them, are listed in [TRIAGE.md](TRIAGE.md).

Vacuums that are only in the Roborock app are not in the Xiaomi cloud and cannot be read by this
integration.

## Reporting a problem

Report bugs on the [fork's issue tracker](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues). Include your vacuum model, the
integration version, the diagnostics (Settings → Devices & services → the integration's entry → ⋮ →
**Download diagnostics**) and a debug log. To turn on debug logging, add this to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.better_xiaomi_cloud_map_extractor: debug
```

Debug logs can contain account tokens. Check the log before posting it and remove anything that
looks like a token, a password or a session cookie.

Problems with the dashboard card belong to the
[card's tracker](https://github.com/Igloczek/lovelace-better-xiaomi-vacuum-map-card/issues).

## Contributing

Pull requests are welcome. [AGENTS.md](AGENTS.md) lists the local checks (`ruff check .` and
`pytest`) and the rules every change follows: a bug fix starts with a test that fails.

## Credits and license

Created by Piotr Machowski as
[Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor), with
contributions from its community. It builds on the
[openHAB miIO add-on](https://github.com/openhab/openhab-addons/tree/main/bundles/org.openhab.binding.miio/src/main/java/org/openhab/binding/miio)
and [Xiaomi Robot Vacuum Protocol](https://github.com/marcelrv/XiaomiRobotVacuumProtocol) by
[@marcelrv](https://github.com/marcelrv), [valeCLOUDo](https://github.com/Xento/valeCLOUDo) by
[@Xento](https://github.com/Xento) and [Valetudo](https://github.com/Hypfer/Valetudo) by
[@Hypfer](https://github.com/Hypfer). Maintained by [Igloczek](https://github.com/Igloczek).

Released under the [MIT license](LICENSE).
