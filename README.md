# Better Xiaomi Cloud Map Extractor

[![Latest release](https://img.shields.io/github/v/release/Igloczek/better-xiaomi-cloud-map-extractor)](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/releases/latest)
[![Tests](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A Home Assistant integration that brings your robot vacuum's live map from the Xiaomi cloud into
Home Assistant — showing rooms, walls, obstacles, the dock, and the robot's live position and path.

It works with Xiaomi, Roborock (Mi Home app), Viomi, Roidmi, Dreame, Ijai and Jonr vacuums.

Part of the **Better** family for Home Assistant vacuums, designed to work together:

- **Better Xiaomi Cloud Map Extractor** (this repository): downloads and draws the live map image
  and room data from the Xiaomi cloud.
- **[Better Xiaomi Vacuum Map Card](https://github.com/Igloczek/lovelace-better-xiaomi-vacuum-map-card)**:
  the dashboard card for this map. Tap rooms to clean them, draw zones, send the robot to a spot,
  and control the vacuum right from the map.

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=Igloczek&repository=better-xiaomi-cloud-map-extractor&category=integration)

<img src="https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/images/map_no_rooms.png" width=48%>  <img src="https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/images/map_rooms.png" width=48%>

## Why it's better

Compared with the original [Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor):

| | Original integration | Better integration |
|---|:---:|:---:|
| 🔑 Cloud session | ❌ expires every 30 days, triggers captcha | ✅ renews automatically in the background |
| 📱 QR, SMS & email login | ❌ broken on HA 2026.9+ & verification dead-ends | ✅ QR, captcha, SMS & email all work |
| 🌐 Cloud outages & bad maps | ❌ asks to log in again or hides entities | ✅ retries quietly and keeps your last map |
| 🔧 Wrong IP, token or format | ❌ "Unknown error", dig through logs | ✅ plain-English message & one-click Repairs |
| ⚡ Home Assistant performance | ❌ freezes HA, downloads docked maps every 10 s | ✅ non-blocking, pauses when docked, no logbook spam |
| 🤖 Ijai & newer Xiaomi vacuums | ⚠️ decryption errors & tiny 800×800 maps | ✅ fixed decryption, map-saving alerts & auto-fit zoom |
| 🧹 Old v2 YAML setups | ❌ crashes on import, leaves broken YAML | ✅ imports cleanly & points out leftover YAML |
| 🔒 Privacy in logs & diagnostics | ⚠️ leaks session tokens, map keys & signed URLs | ✅ secrets automatically redacted |
| 🐛 Bug reports | ✍️ manual forms & logs | 🤖 [your AI agent files them](#reporting-a-problem) |

Installs alongside the original and keeps your login, entity IDs and dashboards: [switching takes a minute](#coming-from-xiaomi-cloud-map-extractor).

## Features

**A map that stays connected**

- **Stays logged in.** Your Xiaomi cloud session renews itself in the background instead of logging
  in with your password every month, so your map doesn't break on unexpected captchas.
- **Smooth sign-in.** Log in by scanning a QR code with the Xiaomi Home app or with your email and
  password — with working support for captchas, SMS and email verification codes, and fast regional
  discovery.
- **Survives cloud hiccups.** Temporary Xiaomi outages, slow regions, or incomplete map files never
  force a new login or blank out your dashboard. Your last good map stays on screen while updates
  retry quietly.

**Problems you can fix in one click**

- **Actionable Repairs.** If your vacuum changes IP address, gets a new token, is set to the wrong
  map format, or has map saving disabled in the Xiaomi Home app, Home Assistant tells you why and
  offers a one-click fix under **Settings → Repairs**.
- **Smart re-authentication.** When Xiaomi really does need your input (a captcha or identity check),
  re-authentication opens directly on that step with the verification link — no hunting through logs.

**Your map, your way**

- **Live map & optional entities.** Creates a ready-to-use **Live map** image entity out of the box.
  Extra entities — a camera, robot and charger coordinates, current room, no-go and no-mop areas, a
  manual refresh button, and an auto-update switch — are available whenever you want to enable them.
- **Full visual customisation.** Change room and obstacle colours, choose which elements to draw,
  adjust icon sizes, rotate or trim the map right from **Configure**.
- **Fit to the explored area (Ijai & Xiaomi S10/S12/S20).** Crops tiny 800×800 floor plans to the
  explored rooms and zooms them cleanly to fill the image (turn on under **Configure → Image
  dimensions**).

**Light on your system & private by default**

- **Responsive Home Assistant.** Map downloads and rendering run in the background without freezing
  Home Assistant. Docked vacuums aren't redownloaded every 10 seconds and unchanged checks don't
  flood your logbook.
- **Quick pickup when cleaning starts.** Supported Viomi, Roidmi and Dreame vacuums check their
  cleaning state through the cloud, so live tracking starts within seconds even across isolated
  networks.
- **Safe to share diagnostics.** Account credentials, session tokens, map decryption keys and signed
  download URLs are stripped automatically from diagnostics and logs.

## Install

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=Igloczek&repository=better-xiaomi-cloud-map-extractor&category=integration)

The button opens this repository in the Marketplace (Home Assistant 2026.11 and newer) or in HACS
(older versions) and offers to add it. If it doesn't open, follow the steps for your setup.

<details>
<summary><b>Marketplace</b> (Home Assistant 2026.11 and newer)</summary>

1. Open **Settings → Marketplace** and accept the first-run warning.
2. **Connect GitHub** when asked. A custom repository can't be added without an account.
3. Add `https://github.com/Igloczek/better-xiaomi-cloud-map-extractor` as an **Integration** and
   install it.
4. Restart Home Assistant.
</details>

<details>
<summary><b>HACS</b> (older Home Assistant)</summary>

1. Open HACS → three-dot menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Igloczek/better-xiaomi-cloud-map-extractor` with type **Integration**.
3. Search for **Better Xiaomi Cloud Map Extractor**, download it and restart Home Assistant.
</details>

<details>
<summary><b>Manual</b></summary>

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
</details>

## Setup

1. Go to **Settings → Devices & services → Add integration** and pick
   **Better Xiaomi Cloud Map Extractor**.
2. Sign in to your **Xiaomi Home / Mi Home** account (not the Roborock app): scan the QR code with
   the Xiaomi Home app, or enter your email and password. Complete a captcha or verification code
   if Xiaomi asks.
3. Pick your vacuum if your account has more than one.
4. Confirm the detected IP address, token and map format (the auto-detected format is marked with
   `*`).

Your **Live map** image entity is ready immediately. Add it to any dashboard or pair it with
**[Better Xiaomi Vacuum Map Card](https://github.com/Igloczek/lovelace-better-xiaomi-vacuum-map-card)**
for interactive room cleaning, zones, and controls. Customise colours, drawn elements, sizes,
rotation and trim anytime under **Configure**.

## Coming from Xiaomi Cloud Map Extractor

This integration installs side by side with the original — nothing is overwritten or deleted. Your
vacuums move over with their settings, Xiaomi login, device and entity IDs, so dashboards,
automations and the map card keep working without changes.

Keep the original integration installed until the final step:

1. [Install](#install) **Better Xiaomi Cloud Map Extractor** alongside the original and restart Home
   Assistant.
2. Go to **Settings → Devices & services → Add integration → Better Xiaomi Cloud Map Extractor** and
   pick **Import from Xiaomi Cloud Map Extractor**. Choose your vacuum and confirm. Any additional
   vacuums then appear under **Discovered** for the same one-click import.
3. The import disables the vacuum's entry in the original integration (reversible at any time —
   nothing is deleted) and moves its device and entities here. No new login is needed.
4. Open **Settings → Repairs** and follow the cleanup steps shown for your vacuum:
   - If an old v2 YAML `camera:` block is still in your configuration, follow the repair's
     instructions to delete that block from the listed file and restart Home Assistant.
   - Delete the disabled entry in **Xiaomi Cloud Map Extractor** (your device and entities stay
     here).
   - Uninstall **Xiaomi Cloud Map Extractor** in Marketplace or HACS.

<details>
<summary><b>Still on v2 (YAML configuration)?</b></summary>

Change `platform: xiaomi_cloud_map_extractor` to `platform: better_xiaomi_cloud_map_extractor` in
your YAML configuration and restart Home Assistant. Your vacuum is imported into a UI integration
automatically using the credentials from your YAML. Then follow the prompt in **Settings → Repairs**
to remove the old YAML block (and sign in again only if Home Assistant asks).

The legacy v2 documentation stays on the
[`v2` branch](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/tree/v2#readme).
</details>

## Supported devices

Works with **Xiaomi**, **Roborock** (paired with the Xiaomi Home / Mi Home app), **Viomi**,
**Roidmi**, **Dreame** and **Ijai** vacuums. The map format is detected automatically from your
model and marked with `*` in the last setup step (**Used map API**). Even if your exact model isn't
listed below, it often works by picking its brand's format there.

> [!NOTE]
> Vacuums paired only with the **Roborock app** are not in the Xiaomi cloud and cannot be read by
> this integration — use Home Assistant's built-in Roborock integration instead.

<details>
<summary><b>Full list of tested models</b></summary>

- **Roborock** (`roborock.vacuum.*`, `rockrobo.vacuum.*`):
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
- **Viomi** (`viomi.vacuum.*`):
  - `viomi.vacuum.v6` (Viomi Vacuum V2 Pro, Xiaomi Mijia STYJ02YM, Mi Robot Vacuum Mop Pro)
  - `viomi.vacuum.v7` (Mi Robot Vacuum-Mop Pro)
  - `viomi.vacuum.v8` (Mi Robot Vacuum-Mop Pro)
  - `viomi.vacuum.v13` (Viomi V3)
- **Roidmi** (`roidmi.vacuum.*`, `zhimi.vacuum.*`, `chuangmi.vacuum.*`, and `viomi.vacuum.v18`, `v23`, `v38`):
  - `roidmi.vacuum.v60` (Roidmi EVE Plus)
  - `viomi.vacuum.v18` (Viomi S9)
  - `viomi.vacuum.v38` (Viomi V5 Pro)
  - `zhimi.vacuum.xa1` (Lydsto R1)
  - `chuangmi.vacuum.hmi707` (IMILAB V1 Vacuum)
- **Dreame** (`dreame.vacuum.*`):
  - `dreame.vacuum.mc1808` (Xiaomi Mi Mop / Xiaomi Mijia 1C)
  - `dreame.vacuum.p2008` (Dreame F9)
  - `dreame.vacuum.p2009` (Dreame D9)
  - `dreame.vacuum.p2028` (Dreame Z10 Pro)
  - `dreame.vacuum.p2029` (Dreame L10 Pro)
  - `dreame.vacuum.p2036` (Trouver LDS Cleaner)
  - `dreame.vacuum.p2041o` (Xiaomi Mop 2 Pro+)
  - `dreame.vacuum.p2140` (Mijia Robot Vacuum-Mop 2C)
  - `dreame.vacuum.p2157` (MOVA L600)
  - `dreame.vacuum.p2259` (Dreame D9 Max)
- **Ijai** (`ijai.vacuum.*`, and `xiaomi.vacuum.b106eu`, `c103`, `d106gl`):
  - Mi Robot Vacuum-Mop 2 Pro (`ijai.vacuum.v3`), Xiaomi S10, S12, S20, and other Ijai models.
- **Xiaomi** (other `xiaomi.vacuum.*` models):
  - Xiaomi H50 (`ov43gb`), M40S (`ov31cn`), and other Xiaomi-format models.
- **XTL** (`xtl.vacuum.xm2216`):
  - Jonr P20 and P20 Pro. The parser comes from
    [Maedrawd's fork](https://github.com/Maedrawd/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor-jonrp20),
    which owners confirmed against the Mi Home app; rooms are coloured but not yet named.

</details>

Models with open support requests and what is needed to add them are tracked in [TRIAGE.md](TRIAGE.md).

## Reporting a problem

Let an AI agent report it. Describe the problem to your agent and it collects your diagnostics,
repairs and logs, removes all private data, works out what it can and files the report. Agent
reports are labelled `agent-report` and handled first, because they come with everything needed to
reproduce the bug.

Give your agent (Claude Code, Codex, Cursor or similar) this instruction, with your problem at the
end:

```text
Read https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/.agents/skills/report-problem/SKILL.md
and follow it to report this problem with my Xiaomi vacuum map:

<what is wrong, e.g. "my Xiaomi S12 map fails to update while cleaning">
```

It asks once for a temporary Home Assistant token, only reads, and shows you the rendered, redacted
report to confirm before posting anything publicly. It never publishes credentials, Xiaomi session
tokens, device tokens, map decryption keys, network addresses, or the map image. The
[step-by-step guide](docs/report-a-problem.md) covers the token, what leaves your computer, and
follow-up questions.

No agent? Use the
[bug form](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues/new?template=bug_report.yml)
and attach your diagnostics (**Settings → Devices & services → Better Xiaomi Cloud Map Extractor →
⋮ → Download diagnostics** — credentials, tokens and map keys are removed automatically).

<details>
<summary><b>How to capture a debug log manually</b></summary>

1. Open **Settings → Devices & services → Better Xiaomi Cloud Map Extractor** and click **Enable
   debug logging**.
2. Reproduce the problem, then click **Disable debug logging** to download the log file.

Alternatively, add this to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.better_xiaomi_cloud_map_extractor: debug
```

Even though this integration redacts secrets, give debug logs a quick look before posting and remove
anything that looks like a password, token or cookie.
</details>

Problems with the dashboard card belong on the
[Better Xiaomi Vacuum Map Card tracker](https://github.com/Igloczek/lovelace-better-xiaomi-vacuum-map-card/issues).

## Documentation

| Topic | Where |
|---|---|
| Reporting a bug | [Report a problem](docs/report-a-problem.md) |
| Release notes | [Changelog](CHANGELOG.md) |
| What is planned | [Roadmap](ROADMAP.md) |
| Technical differences from the original | [Fork details](FORK.md) |
| Pending model support & upstream triage | [Triage](TRIAGE.md) |
| Legacy v2 YAML documentation | [`v2` branch](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/tree/v2#readme) |

## Contributing

Pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers setup, local checks
(`ruff check .` and `pytest`), and adding or fixing vacuum formats; [AGENTS.md](AGENTS.md) holds the
full project map and agent workflow.

## Credits and license

Created by Piotr Machowski as
[Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor),
with contributions from its community. It builds on the
[openHAB miIO add-on](https://github.com/openhab/openhab-addons/tree/main/bundles/org.openhab.binding.miio/src/main/java/org/openhab/binding/miio)
and [Xiaomi Robot Vacuum Protocol](https://github.com/marcelrv/XiaomiRobotVacuumProtocol) by
[@marcelrv](https://github.com/marcelrv), [valeCLOUDo](https://github.com/Xento/valeCLOUDo) by
[@Xento](https://github.com/Xento) and [Valetudo](https://github.com/Hypfer/Valetudo) by
[@Hypfer](https://github.com/Hypfer). Maintained by [Igloczek](https://github.com/Igloczek).

Released under the [MIT license](LICENSE).
