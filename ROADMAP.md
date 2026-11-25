# Roadmap

What this fork plans to work on next. The current state is described in the [README](README.md);
released changes are in [CHANGELOG.md](CHANGELOG.md); the differences from upstream are in
[FORK.md](FORK.md); every open upstream issue and pull request is classified in
[TRIAGE.md](TRIAGE.md).

## How to propose work

Open an issue on the [fork tracker](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues)
with the vacuum model, the integration and Home Assistant versions, a debug log with tokens removed,
and what you expected. A change is planned when it can be proven: by a test with a scrubbed fixture
of the cloud response or map, or by someone with the vacuum testing a pre-release. Every change comes
with a test that fails first (see [AGENTS.md](AGENTS.md)).

## In progress

- **Xiaomi models: live data and map index** (fork PR #18, pre-release for b108gl, ov21gl and ov81gl
  owners): upstream PRs #781, #750 and a generalised #748. Robot position and path, the map index,
  the current map through the cloud when the LAN does not answer, the status property of models such
  as the d109gl and the H50 (`xiaomi.vacuum.ov43gb`), ov21gl rooms and S40 zones. Closes #563, #588,
  #657, #702, #704, #720, #739.
- **Roborock map without the local network**: read the map name through the cloud when Home
  Assistant cannot reach the vacuum, as Ijai does for the WiFi serial (#601, and with fork PR #18
  #381).
- **Jonr P20** (fork PR #28, pre-release for a P20 owner): `xtl.vacuum.xm2216` with the parser from
  Maedrawd's fork (#621). Next for it: a real map as a fixture (scrubbed), room names, and the run
  state through the cloud once its MIoT property is known.
- **Roidmi sdj60** (fork PR #28, pre-release for an sdj60 owner): the map from
  `get_interim_file_url_pro` and the bitmap split from upstream PR #768 (#572).

Unverified: an unencrypted API answer with code 2 or 3, or the message `invalid signature`, is
treated as an expired session (renewal, then password login). That mirrors other Xiaomi cloud
clients, not a captured response; replace it with a scrubbed capture of what Xiaomi really sends for
an expired `serviceToken` (#697 reports `{"code":0,"message":"SERVICETOKEN_EXPIRED"}`, which is
handled).

## Next, in order

Ranked by the north star in [AGENTS.md](AGENTS.md#north-star): reliability of the cloud session and
map download first, then failures the UI makes actionable, then the drop-in from the original, then
the rest of the upstream backlog. Within a priority, work that hits more users goes first (comments
plus 👍 on the upstream items, from [TRIAGE.md](TRIAGE.md), taken on 2026-11-25). Issue and PR
numbers refer to the upstream repository; each item names the priority it serves.

1. **Xiaomi: map path from the vacuum's own uid** (P1, #762). The map object can sit under a uid
   other than the logged-in account's (seen on the H40 `xiaomi.vacuum.ov51gl`); the connector keeps
   only the last part of the object name the vacuum reports and rebuilds the path with the account's
   uid, so the signed URL ends in "404 Object Not Found". Fork PR #18 keeps that behaviour
   (`object_name`). Use the full object name from the MIoT property; test with a scrubbed property
   answer.
2. **Login dead-ends** (P2, #700, #712, #780, PR #793; 45 users). A two-factor code that ends in
   "Unexpected error" after minutes, a retry that asks only for the captcha, the finish location after
   `verifyEmail`, and the cloud region choice from PR #793. Each needs a recorded, scrubbed login
   exchange as a fixture.
3. **Drop-in follow-up** (P3): decide whether to raise a repair issue that points users of the
   original at the import once this integration is running. (The import itself was verified end to
   end on a real Home Assistant, with the original installed and then removed, on 2026-11-24.)

## 1.0.0 criteria

1.0.0 is the maintainer's decision. It is due when both hold:

1. **Upstream backlog done.** Every open upstream issue and pull request is analysed (imported,
   fixed, ported or recorded under **Not planned** with a reason), and the biggest bugs above are
   fixed.
2. **Deep refactor done.** The long monolithic files are split into focused modules with clear
   boundaries: cloud connector (login, session, encrypted API), per-vendor map APIs, coordinator,
   entities, config flow. Tests cover everything the refactor touches.

When both hold, an agent brings the maintainer one question, "ready for 1.0.0?", with the evidence:
the [triage table](TRIAGE.md), the fixed issues, the module map and the test coverage.

## Later

- **Ijai map saving off: confirm with an owner.** The message relies on `remember-state` (siid 10,
  piid 1) reading 0 when map saving is off in Xiaomi Home; that comes from the miot-spec and #759,
  not a captured answer. Replace the fixture with a scrubbed `get_properties` answer from an owner,
  and record what the cloud's map endpoints return in that state.

- **Room names outside ASCII.** Every vendor draws room names with Pillow's default font (Aileron),
  which has no glyphs beyond ASCII, so "Salon" renders but "Oturma odası" or "Łazienka" show boxes
  where the letters are (seen while porting upstream PR #794, which bundles DejaVu Sans, about
  760 KB). Options: a system font when Home Assistant's image has one, otherwise a bundled subset
  of DejaVu; test with a room name per script through the real parser.
- **Ijai fit on by default for new setups.** "Fit to the explored area" is off for everyone (fork
  PR for upstream #794). New Ijai setups could start with it on; the maintainer's call.
- **Waiting for a capture**: 20 upstream items need a raw map, a debug log or a tester before they
  can be fixed with a test; [TRIAGE.md](TRIAGE.md) says what each needs. The biggest groups: Dreame
  encrypted maps ("incorrect header check", #459 with #571, #607, #608; md1808 #450 with #757, #772,
  where Repairs now says the map can't be decrypted yet: the IV and a map key are needed) and Viomi maps (#315, #534,
  #613, #746); Xiaomi models not yet confirmed on v3 (#558, #602, #706, #751); Roidmi v62 and v66
  (#477, #710); the Dreame fresh-map request (#655: python-miio maps `map_view` as a property, so
  `call_action("map_view")` raises before anything is sent; needs a p2008 owner, pre-release).
- **README for v3 in depth**: options flow, every entity and attribute, and the card integration.
- **Real Home Assistant smoke test**: install the release zip into a Home Assistant container and
  set it up against a recorded cloud, like the card's end-to-end suite.
- **Lint baseline**: widen the ruff rules (pyupgrade, import order, bugbear `B904`) in one dedicated
  change.
- **Undeclared dependency**: `vacuum-map-parser-ijai` imports `google.protobuf` without declaring it;
  decide whether `manifest.json` lists `protobuf` or the parser is fixed.
- **Default HACS listing** once the first stable release is out.

## Feature requests kept

Multi-floor maps (#67, #350), trim by coordinates and negative trim (#137, #46), background image
(#39), carpets and room order (#185), auto-rotating a map the vacuum flips (#303), Lydsto W2 path
(#419), cleaning history map (#633), RGBA room colours (#652), Deerma vacuums (#687, #651, #738), the
d103cn's live-frame map (#765).

## Not planned

The 38 v2-only or obsolete items (v2 YAML login, the v2 camera platform and its blueprints, models
that already work on v3) are listed with their reason in [TRIAGE.md](TRIAGE.md).

| Item | Why |
| --- | --- |
| Roborock-app-only vacuums (#100, #156, #388, #461, #489, #528) | Those devices are not in the Xiaomi cloud; Home Assistant's Roborock integration covers them. |
| PR #770 (IJAI/Xiaomi retrieval rewrite) | Changes the encrypted request format for every vendor; the targeted Ijai PRs cover the same bugs. |
| PRs #749, #527, #289 | Wrong base or superseded by the v3 code and fork PR #18. |
| Cairo instead of Pillow (#102) | Drawing lives in the parser packages; it already runs off the event loop and only when the map changes. |
| Echo Show stream (#112) | Needs an RTSP stream; the integration serves still images. |
| python-miio "unsupported model" warning (#490, #474) | Harmless; the map works. |
| 3D map (#574) | The app draws it; the cloud has no 3D map. |
| Deleting maps (#734) | Writes to the vacuum; this integration only reads. |
| `scripts/map_processor.py` README fix (PR #591) | The script has not run since v3 (it imports modules removed in 2023) and was never in the release zip; the fork removed `scripts/`. |
