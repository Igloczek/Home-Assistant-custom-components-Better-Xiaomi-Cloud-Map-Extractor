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

- **Ijai maps (the largest v3 cluster).** Upstream PRs #725, #782, #787, #792, #785, #789 and #786
  in one change, shipping as a pre-release for owners to test. Closes #682, #696, #698, #703, #709,
  #714, #742, #759.
- **Re-authentication crash.** Reauth reaches `confirm_data` without an existing entry and ends in
  "Unknown error occurred". Includes upstream #730 and the regression test from upstream PR #754.
- **Cloud login robustness.** Telling a Xiaomi outage (5xx, HTML, undecodable body) from a refusal,
  keeping the session through a failed login, and throttling background password logins are in
  review. Still open: PR #793 (cloud region selection), #700, #712 and the `verifyEmail` finish
  location in #780. Unverified: an unencrypted API answer with code 2 or 3, or the message
  `invalid signature`, is treated as an expired session (renewal, then password login). That
  mirrors other Xiaomi cloud clients, not a captured response; replace it with a scrubbed capture
  of what Xiaomi really sends for an expired `serviceToken`.

## Next, in order

Ranked by the north star in [AGENTS.md](AGENTS.md#north-star): reliability of the cloud session and
map download first, then failures the UI makes actionable, then the drop-in from the original, then
the rest of the upstream backlog. Within a priority, work that hits more users goes first (upstream
issues + comments + 👍; the backlog on 2026-11-24 was 141 open issues and 27 open pull requests),
counting only what still happens on v3.0.0-alpha-24. Issue and PR numbers refer to the upstream
repository; each item names the priority it serves.

1. **Secrets in diagnostics and vendor logs** (reliability: a leaked session is a broken session).
   The cloud connector's log is covered by `tests/test_secrets_in_logs.py`; still open: diagnostics
   export the map decryption key in `device_data`. Extend the log scan to every other
   vendor's map download (Ijai is covered). Closes #778.
2. **Event-loop blocking and update churn** (reliability). Synchronous python-miio calls run on the event loop,
   `MiotDevice` is created without a mapping (warning spam, #791), and the map keeps refreshing while
   docked (#764) and floods the logbook (#719). Import #775 (skip #769, which it covers). Closes #791,
   #764, #719; check #655 and #601 against it.
3. **Actionable failures everywhere** (UI). Any connector error that is not a login problem ends
   as an `UpdateFailed` with a log line only. Map download and parse failures that persist should
   raise a repair issue that says what to do (vacuum offline, region; `repairs.py` already does this
   for the wrong map format), and the config flow should never end in "Unknown error". Starts after
   the reauth and login threads merge, building on their error mapping.
4. **Xiaomi models: live data and map index** (backlog). Robot position and path missing while cleaning, wrong
   map after maps were deleted, missing walls and no-go zones (b108gl, ov43gb, ov81gl, S40 Pro).
   Import #781 (skip #750, which does the same in 976 lines) and a generalised #748 (ov21gl rooms
   with `room_id=0`, zones, walls; skip #749, wrong base). Closes #720, advances #563, #657, #702,
   #739.

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
local host (#381), RGBA room colours (#652), S40 walls and no-go zones (#739), a notification when
re-authentication is needed (#338, #440).

## Not planned

| Item | Why |
| --- | --- |
| v2 YAML login failures (#157, #422, #533, #583, #629, #644, #645, #697, #752, #753, #760) | v2 is frozen; v3 login replaces it. #697 is covered by the login thread. |
| Other v2-only display and blueprint issues (#20, #71, #75, #173, #236, #339, #351, #426, #462, #463, #487, #504, #507, #514, #536, #539, #540, #547, #562, #605) | Only on the v2 camera platform. |
| Roborock-app-only vacuums (#100, #156, #388, #461, #528) | Those devices are not in the Xiaomi cloud. |
| PR #770 (IJAI/Xiaomi retrieval rewrite) | Changes the encrypted request format for every vendor; the targeted Ijai PRs cover the same bugs. |
| PRs #749, #767, #527, #289 | Wrong base, junk, or superseded by the v3 code. |
