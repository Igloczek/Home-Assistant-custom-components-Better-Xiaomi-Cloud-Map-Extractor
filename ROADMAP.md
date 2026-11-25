# Roadmap

What this fork plans to work on next. The current state is described in the [README](README.md);
released changes are in [CHANGELOG.md](CHANGELOG.md); the differences from upstream are in
[FORK.md](FORK.md).

## How to propose work

Open an issue on the [fork tracker](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues)
with the vacuum model, the integration and Home Assistant versions, a debug log with tokens removed,
and what you expected. A change is planned when it can be proven: by a test with a scrubbed fixture
of the cloud response or map, or by someone with the vacuum testing a pre-release. Every change comes
with a test that fails first (see [AGENTS.md](AGENTS.md)).

## In progress

- **Xiaomi models: live data and map index** (backlog): upstream PR #781 and a generalised #748, in
  a pre-release until owners of the S20+ (b108gl), an ov21gl and an S40 (ov81gl) confirm the robot,
  path, rooms and forbidden areas. Closes #720, advances #563, #657, #702, #739.
- **Viomi: follow cleaning start sooner**: run state through the cloud, waiting for a check on a
  Viomi V8 (the cloud's `get_prop` answer is modelled, not captured).

Still open from the cloud login work: PR #793 (cloud region selection), #700, #712 and the
`verifyEmail` finish location in #780. Unverified: an unencrypted API answer with code 2 or 3, or the
message `invalid signature`, is treated as an expired session (renewal, then password login). That
mirrors other Xiaomi cloud clients, not a captured response; replace it with a scrubbed capture of
what Xiaomi really sends for an expired `serviceToken`.

## Next, in order

Ranked by the north star in [AGENTS.md](AGENTS.md#north-star): reliability of the cloud session and
map download first, then failures the UI makes actionable, then the drop-in from the original, then
the rest of the upstream backlog. Within a priority, work that hits more users goes first (upstream
issues + comments + 👍; the backlog on 2026-11-24 was 141 open issues and 27 open pull requests),
counting only what still happens on v3.0.0-alpha-24. Issue and PR numbers refer to the upstream
repository; each item names the priority it serves.

## 1.0.0 criteria

1.0.0 is the maintainer's decision. It is due when both hold:

1. **Upstream backlog done.** Every open upstream issue and pull request is analysed (imported,
   fixed, ported or recorded under **Not planned** with a reason), and the biggest bugs above are
   fixed.
2. **Deep refactor done.** The long monolithic files are split into focused modules with clear
   boundaries: cloud connector (login, session, encrypted API), per-vendor map APIs, coordinator,
   entities, config flow. Tests cover everything the refactor touches.

When both hold, an agent brings the maintainer one question, "ready for 1.0.0?", with the evidence:
the triage table, the fixed issues, the module map and the test coverage.

## Later

- **Xiaomi H50 status** (`xiaomi.vacuum.ov43gb`): its status is siid 2 piid 2, which
  vacuum-map-parser-xiaomi does not map, so it falls back to the once-a-minute map check like a
  vacuum without status. Needs the property values from the miot-spec.
- **Dreame: ask for a fresh map** (#655). On non-encrypted Dreame models (`dreame.vacuum.p2008`)
  `get_map_name` asks the vacuum for a new map with python-miio `call_action("map_view")`, but
  python-miio maps `map_view` as a property, not an action, so it raises before anything is sent and
  the cloud keeps the map from the last upload (old robot position). Needs a Dreame owner to confirm
  the right action (siid 6, aiid 1 as the encrypted models use through the cloud?); pre-release.
- **Xiaomi and Roborock without the local network.** Both read the map name over the LAN, so a
  vacuum that is not reachable from Home Assistant gets no map at all; a cloud fallback for the map
  name (as Ijai does for the WiFi serial) would fix it. Related: #601 (rockrobo.vacuum.v1, v2).

- **Dreame encrypted maps** ("incorrect header check" on p2140/p2150/r22xx) and Viomi V5 Pro
  (#746, reported on alpha-24). Needs a captured map from a reporter.
- **Roidmi sdj60**: port v2 PR #768 to `vacuum_roidmi.py` (#572).
- **Ijai rendering**: #794 (fit to explored area), without the bundled font if possible.
- **Drop-in follow-ups**: verify the import on a real Home Assistant with the original installed
  and then removed (the pytest suite covers the registry moves); decide whether to raise a repair
  issue that points original users at the import once this integration is running.
- **Small imports**: #591 if
  `scripts/` keeps shipping; check #636 (homes without `share_home_list`) against v3 discovery.
- **README for v3 in depth**: options flow, every entity and attribute, and the card integration.
- **Real Home Assistant smoke test**: install the release zip into a Home Assistant container and
  set it up against a recorded cloud, like the card's end-to-end suite.
- **Lint baseline**: widen the ruff rules (pyupgrade, import order, bugbear `B904`) in one dedicated
  change.
- **Undeclared dependency**: `vacuum-map-parser-ijai` imports `google.protobuf` without declaring it;
  decide whether `manifest.json` lists `protobuf` or the parser is fixed.
- **Default HACS listing** once the first stable release is out.

## Feature requests kept

Multi-floor maps (#67, #350), rotation by any angle and trim by coordinates (#137, #377), background
image (#39), carpets and room order (#185), cleaning history map (#633), cloud-only mode without a
local host (#381), RGBA room colours (#652), a notification when
re-authentication is needed (#338, #440).

## Not planned

| Item | Why |
| --- | --- |
| v2 YAML login failures (#157, #422, #533, #583, #629, #644, #645, #697, #752, #753, #760) | v2 is frozen; v3 login replaces it. #697 is covered by the login thread. |
| Other v2-only display and blueprint issues (#20, #71, #75, #173, #236, #339, #351, #426, #462, #463, #487, #504, #507, #514, #536, #539, #540, #547, #562, #605) | Only on the v2 camera platform. |
| Roborock-app-only vacuums (#100, #156, #388, #461, #528) | Those devices are not in the Xiaomi cloud. |
| PR #770 (IJAI/Xiaomi retrieval rewrite) | Changes the encrypted request format for every vendor; the targeted Ijai PRs cover the same bugs. |
| PRs #749, #767, #527, #289 | Wrong base, junk, or superseded by the v3 code. |
| PR #750 (S20+ live map) | Does what the imported #781 does, in 976 lines. |
