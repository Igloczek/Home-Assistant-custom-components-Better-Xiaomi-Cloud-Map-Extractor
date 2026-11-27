# Changelog

## Unreleased

### Fixed

- A vacuum added while its model wasn't supported yet gets a repair as soon as a release can read its
  map: Settings → Repairs → "its map can now be shown" → Submit switches it to the new map format.
  Entities, options and login stay as they are. Before, it stayed unsupported until it was deleted and
  added again.
- An entry set to a map format this version doesn't know (for example `XTL`, stored by another fork of
  the original, or a format from a newer release after a downgrade) sets up and shows the vacuum as
  unsupported instead of failing with `ValueError`. The stored value is kept.
- A vacuum set up in Xiaomi Cloud Map Extractor (or another fork of it) in a map format this version
  can't read, such as `XTL` from the Jonr P20 fork, is no longer offered for import, and importing it
  from the menu stops with a message instead of disabling a map that works there.
- On a Xiaomi map where only some room ids repeat, room ids stay as the vacuum sends them, so room
  cleaning from the map card targets the right rooms again and room names stay on their own rooms.
  Since 1.0.2 every room of such a map was renumbered by its grid id.
- Rooms of the `xiaomi.vacuum.ov21gl` are always keyed by their grid id, also on a one-room map, so
  their ids no longer change when a room is added.
- A live cleaning path whose points mostly fall outside the map is no longer drawn, so a vacuum
  whose path record the integration misreads cannot draw lines across the map.

## 1.0.6 - 2026-11-27

### Fixed

- Xiaomi vacuums that upload their map unencrypted, such as the Mijia M30 Pro (`xiaomi.vacuum.c107`),
  show their map. Before, the Live map stayed `unknown`. Encrypted maps of every other Xiaomi model are
  read exactly as before; the new decoding is only tried when decryption fails.

## 1.0.5 - 2026-11-27

### Fixed

- Connections to the Xiaomi cloud are always released, including failed map downloads and login
  redirect hops, so a long-running Home Assistant no longer slowly leaks connections.
- Downloading diagnostics no longer fails before the first map has been fetched.
- The map entities no longer keep the account password, username or device token in memory, and
  camera and image entities serve the cached map without an extra thread hop.
- Removing an entry clears its failure counters, and the update coordinator is tied to its config
  entry as Home Assistant expects.
- `manifest.json` in the repository carries the released version instead of upstream's `v3.0.0-beta`,
  so a manual install shows the right version.

## 1.0.4 - 2026-11-27

### Changed

- The leftover v2 YAML repair (`original_yaml`) is now instruction-only: it still names the exact
  file and line and shows the redacted YAML block to delete, without modifying user YAML files or
  writing backup copies (following HACS review feedback).

### Fixed

- Cloud sessions now use Home Assistant's configured timezone (`homeassistant.util.dt.get_default_time_zone()`)
  instead of `tzlocal`, avoiding an unlisted third-party import and timezone disk reads on the event
  loop.

## 1.0.3 - 2026-11-26

### Changed

- The AI agent problem-reporting skill (`.agents/skills/report-problem/SKILL.md`) and [`docs/report-a-problem.md`](docs/report-a-problem.md) now always show the full rendered, redacted report (including diagnostics, options, room names and log lines) and wait for explicit user confirmation before filing or commenting on GitHub (following HACS review feedback).

## 1.0.2 - 2026-11-25

### Fixed

- Xiaomi vacuums show the robot and its path while cleaning. The cloud map has neither, so they
  are read from the vacuum (position and the current clean's trajectory) and drawn on the map; the
  S20+ (`xiaomi.vacuum.b108gl`) map now refreshes during a clean instead of treating "Sweeping" as
  idle (upstream PR #781 by muryoh, extended to every Xiaomi model that reports them; upstream #720,
  #563; confirmed on `xiaomi.vacuum.b108gl` by LaZyCraZy71 on #20).
- Xiaomi vacuums read their status from the right property: on the S40, X20+, H50, 5 Pro and most
  newer models it was read from the firmware version, so the map could not tell cleaning from
  docked.
- A Xiaomi vacuum that Home Assistant cannot reach on the local network keeps showing the current
  map: the map number and the robot position come through the Xiaomi cloud, or the vacuum's last
  reported map is kept. A vacuum that never said which map is current is reported unreachable
  instead of showing an old, deleted map; a map number missing from the vacuum's map name is read
  from its current map id (upstream #720).
- A Xiaomi vacuum that stores its maps under another Xiaomi account id than the one logged in, as
  an H40 (`xiaomi.vacuum.ov51gl`) does, gets its map instead of "404 Object Not Found": the map is
  downloaded from the path the vacuum reports (upstream #762).
- Rooms of the Xiaomi 5 Pro (`xiaomi.vacuum.ov21gl`) and any other map that repeats a room id stay
  separate instead of merging into one room (upstream PR #748 by Luciano Benitez, generalised;
  upstream #702, #657).
- No-go zones, no-mop zones and virtual walls of the S40 (`ov81gl`), 5 Pro and other Xiaomi models
  that store them as point lists are drawn (`vacuum-map-parser-xiaomi` 0.1.4; upstream #739, PR
  #748).

## 1.0.1 - 2026-11-25

### Fixed

- Ijai vacuums (`ijai.vacuum.*` and Xiaomi models on the Ijai map format) no longer fail with `TypeError: aes_decrypt() takes 2 positional arguments but 3 were given` when installed side by side with the original `xiaomi_cloud_map_extractor` (whose `v3.0.0-alpha-24` pins `vacuum-map-parser-ijai==0.1.0`). Map decryption now runs directly via `pycryptodome`, so both key formats work whether parser `0.1.0` or `0.1.1` is loaded (#9, PR #49; verified on `ijai.vacuum.v3` by Altar82).

### Changed

- Rewrote `README.md` for end users with a side-by-side comparison table and clearer installation and migration steps (PR #39).
- Added AI-agent-first problem reporting (`report-problem` skill, zero-dependency Python collector/redactor CLI, and automated issue triage, PR #40).

## 1.0.0 - 2026-11-25

The first stable release. Every open issue and pull request of the original is analysed in
[TRIAGE.md](TRIAGE.md), and the code is split into focused, tested modules.

### Fixed

- A Roborock, Viomi or Roidmi map that can't be read (corrupt or cut off) now says the map couldn't be read and keeps the last map, instead of "Unexpected error" with every entity unavailable.
- Options: entering a room id that is not a number in **Room colors** shows the error again instead of an empty next step that cleared every room colour; stored room colours show in the colour picker.
- Options: **Sizes** fills in every size, so the form can be saved without typing each one.
- The **Goto predicted path** sensor no longer fails when the vacuum reports a predicted path.
- QR login: an unexpected answer or error while getting the code ends on the retry form instead of "Unknown error".
- Dreame models with encrypted maps: a status answer without a time no longer breaks the next map request.
- `protobuf` is installed with the integration; Ijai map parsing needs it and some installations did not have it.
- A map download that hangs gives up after a minute instead of five, and one that breaks off midway keeps the last map instead of "Unexpected error".
- Dreame and Ijai maps that unpack but can't be parsed keep the last map instead of "Unexpected error".
- Logging in with a verification code no longer dead-ends:
  - after the emailed code, setup finishes even when Xiaomi sends the login on through a regional
    account address (seen on `ru` accounts) or skips a step, instead of failing with "Unable to
    determine finish location after verifyEmail" (upstream #780);
  - accounts verified by phone get the code by SMS; before, an email was requested that never came;
  - a mistyped code asks for the code again, and the form says so when Xiaomi did not send a code
    (then the verification page linked on the form is the way), instead of "check the credentials".
- After the code, setup no longer sits for minutes and then fails when a Xiaomi region never answers
  (for example `us` from Russia): each region gets 20 seconds and the vacuums of the others are
  listed (upstream #700). This also covers what the region choice of upstream PR #793 by MTrab worked
  around, so no extra setup step was added.
- A wrong password typed while Xiaomi asks for a captcha now goes back to the username and password
  form ("Xiaomi rejected the login") instead of asking for the captcha again and again; this also
  covers re-authentication after a password change (upstream #712).

### Changed

- Docs: the README's switch from Xiaomi Cloud Map Extractor matches what the import does (install
  next to the original; Repairs then lists what is left), the issue forms carry this integration's
  name and ask for diagnostics instead of a YAML configuration, and the "moved from Xiaomi Cloud Map
  Extractor" repair points at the one-click removal of the leftover YAML.

## 0.1.6 - 2026-11-25

### Changed

- The Repairs issue about the original's leftover YAML removes the block in one click: a backup copy of the file is saved next to it, only that block goes, and nothing needs a restart. When the file can't be changed safely, it says what to delete, as before. The issue title no longer repeats the integration name.

## 0.1.5 - 2026-11-25

### Fixed

- A vacuum imported from v2 YAML with `force_api` set now loads; before, setup failed with `ValueError: 'viomi' is not a valid VacuumApi`.
- The cloud connection no longer uses `locale.getdefaultlocale`, which Python 3.15 removes; the locale sent to Xiaomi stays the same, and `LC_CTYPE=utf-8` no longer fails.

### Changed

- Internal: the cloud connector, config flow, repairs, v2 YAML import and map update are split into focused modules; no behaviour change.

## 0.1.4 - 2026-11-25

### Added

- Ijai vacuums (and the Xiaomi models on the Ijai map format) can fit the map to the part the vacuum
  has explored: turn on **Configure → Image dimensions → Fit to the explored area**. The vacuum
  uploads a fixed 800×800 canvas however little of it is explored, so the floor plan used to be a
  small island in a large background. With the option on, the map is cropped to the explored area
  and enlarged to about 960 px, with the vacuum, charger, paths and room names enlarged with it. Your
  trim and scale still apply when they cut or enlarge more. It is off by default, so existing maps
  look the same until you turn it on. Based on upstream PR #794 by Korkuttum, without its bundled
  font and transparent background.
- Failures that keep happening show up under Settings → Repairs with a one-click fix, and go away by
  themselves once the problem is gone:
  - the vacuum is not in the configured Xiaomi region: the fix finds the region (and IP address and
    token) that has it and moves the entry there, without logging in again;
  - the vacuum refuses the stored token, or has not answered on the local network for 5 minutes: the
    fix offers the IP address and token Xiaomi Cloud lists now (the stored ones when Xiaomi can't be
    reached);
  - the map download keeps failing: the fix checks the region and address, and says what to do when
    they are already right.

  A login is asked for only when the saved session can't be renewed or the account no longer has
  the vacuum.
- Ijai vacuums (and the Xiaomi models on the Ijai map format, such as the S12) say when map
  saving is turned off in the Xiaomi Home app, instead of failing with "Padding is incorrect" or a
  generic map failure (upstream #759). The update message and a Repairs issue say to turn on map
  saving in the Xiaomi Home app; the last map stays on screen meanwhile, and the issue goes away
  with the next map.

### Removed

- `scripts/map_processor.py`, the offline map renderer, which has not worked since the v3 rewrite and
  was never part of the installed integration. (Upstream PR #591, a fix to its README, is therefore
  not imported.)

### Fixed

- A Dreame vacuum whose map Xiaomi Cloud sends encrypted, in a way the integration can't decrypt yet
  (dreame.vacuum.md1808, the Xiaomi Robot Vacuum-Mop 2C), failed every update with "The map update
  failed unexpectedly" ("incorrect header check" in the log, upstream #450, #757, #772). The update
  message and a Repairs issue now say that the map of this model is encrypted and can't be decrypted
  yet, so nothing in your configuration needs changing, and where support for the model is tracked.
  Any other Dreame map that can't be read now shows "The downloaded map could not be read" and is
  retried, instead of an unexpected error.
- When Xiaomi Cloud or the map storage did not answer in time, the "Failed to update" message and the
  log quoted the whole request address, including the session secret (`ssecurity`) or, during a
  login, the `clientSign`. They now show only the host and path. If you posted such a message
  publicly, change your Xiaomi account password to end that session.
- Encrypted Dreame maps: the map decryption key no longer appears in debug logs (the map parser logged
  it).
- After importing a vacuum that came from v2 YAML, Repairs now says to remove the old
  `camera: - platform: xiaomi_cloud_map_extractor` block: it shows the file, the line and the block
  (username, password and token hidden) and that removing it is safe. Left in place, it made Home
  Assistant log "Platform error: camera - Integration 'xiaomi_cloud_map_extractor' not found" at
  every start once the original was uninstalled. The "moved from Xiaomi Cloud Map Extractor" repair
  lists removing the block before uninstalling the original.
- No more "Detected blocking call" warnings: the status and map-name requests to the vacuum on the
  local network, and the map decoding and drawing, run outside Home Assistant's event loop, so a slow
  or unreachable vacuum no longer freezes Home Assistant for seconds on every update (upstream PR
  #775 by tovisb, which also covers PR #769 by its author).
- "Neither the class nor the parameter defines the mapping" is no longer logged at every start for
  Xiaomi and Ijai vacuums (upstream #791).
- A docked vacuum's map is no longer downloaded and redrawn every 10 seconds (upstream #764).
  Vacuums that report their status stop after the usual few updates once docked; vacuums that
  cannot (Viomi, Roidmi, Dreame, and the Xiaomi H50, whose status property holds the firmware
  version) are downloaded every update only while the map changes, and checked once a minute after
  three unchanged downloads.
- A Viomi (V6, V7, V8, V10, V13), Roidmi Eve Plus (v60) or Dreame (1C, F9, D9, Z10 Pro, Mop 2
  family) that starts cleaning is followed from the next update (10 seconds) instead of up to a
  minute late: the vacuum's run state is read through the Xiaomi cloud on every update, so it works
  even when Home Assistant can't reach the vacuum on the local network. When the cloud can't tell,
  the map is checked once a minute as before.
- Updates that bring the same map no longer write new states to the recorder and logbook (upstream
  #719). The `last_update_timestamp` and `last_successful_update_timestamp` attributes now change
  with the map, not with every check; diagnostics still show the last check.
- An Ijai vacuum that Home Assistant cannot reach on the local network keeps getting new maps from
  the cloud instead of keeping the first one.
- A map that failed to download at startup is retried while the vacuum is docked, instead of never.
- Roborock no longer waits after a successful map name request.
- A vacuum that is not in the configured region, or refuses the stored token, no longer starts
  re-authentication: the login was never the problem. The Repairs issue above handles it.
- Setup no longer finishes with a session that could not be saved, which made every restart ask for
  a login: the last step shows an error and one click tries again.

## 0.1.3 - 2026-11-24

### Fixed

- Setup no longer ends in "Unknown error". When the login worked but the vacuum list could not be
  loaded, a form says why (Xiaomi unreachable, session refused, unexpected answer) and one click
  tries again; a login that fails because Xiaomi can't be reached says so instead of asking you to
  check the password.
- A Xiaomi region that is down no longer reads as "No vacuums found" during setup.
- Every failed map update has a translated reason (Xiaomi unreachable, vacuum not answering on the
  network, login paused to avoid a captcha, ...), including errors that used to escape as an
  unhandled exception.

## 0.1.2 - 2026-11-24

### Fixed

- QR code login works on Home Assistant 2026.9 and later (upstream #779): the QR code and link
  stay visible while waiting instead of `MISSING_VALUE` (upstream PR #795 by david-romero), the
  flow finishes once the login is approved, and the saved session keeps the `cUserId` Xiaomi needs
  to list the vacuums after a restart (upstream PR #796 by dzabel). Works in re-authentication too.
- The QR login link opens Xiaomi's sign-in page in a signed-out browser instead of a JSON document
  (upstream PR #766 by stephenwilley).
- A QR code that expires, a declined login or Xiaomi being unreachable ends in a clear message,
  and one click shows a new code; the wait for the scan lasts as long as the code is valid
  instead of 60 seconds.

## 0.1.1 - 2026-11-24

### Added

- When a vacuum of another vendor (Roborock, Dreame, Viomi, ...) is set to the Xiaomi map format
  and its map cannot be read, Settings → Repairs offers to switch it to its own format in one
  click. Vacuums whose map works are never touched.

### Fixed

- Vacuums set up from v2 YAML (`platform: better_xiaomi_cloud_map_extractor`) get the vacuum's MAC
  as unique id, and setup fills it in for existing ones, so Home Assistant recognises the vacuum as
  already set up.
- Diagnostics no longer include the map decryption key or the signed map download URL (upstream
  #778).

## 0.1.0 - 2026-11-24

First release of this fork under its own name, based on upstream v3.0.0-alpha-24. It installs
next to the original as `better_xiaomi_cloud_map_extractor`.

### Added

- The Xiaomi cloud session renews itself with the stored passToken instead of logging in again
  with your password, so the map keeps working past the session's 30 days without a captcha or
  identity check (upstream PR #777 by chrodriguez). The session is saved after every renewal.
- When Xiaomi does need you, re-authentication says why: separate screens for a captcha and for
  identity verification (with the link to open), a one-click "log in again with the saved
  username and password", and a two-factor step that shows the verification link.
- Import from Xiaomi Cloud Map Extractor: vacuums set up in the original integration move over
  with their settings, Xiaomi login, device and entity ids, so dashboards and automations keep
  working. This includes vacuums the original imported from v2 YAML; one whose Xiaomi lookup
  failed back then asks to log in once after the import. The import works while the original is
  still installed: it disables the original's entry (nothing is deleted), and a repair then tells
  you when you can delete that entry and uninstall the original.

### Fixed

- Xiaomi-format maps no longer fail with "Incorrect AES key length" on models such as
  xiaomi.vacuum.c107, d101 or b112: the key is the last 16 characters of the model, as in the
  Xiaomi Home app. Models whose map already worked use the same key as before.
- A vacuum imported from v2 YAML without `force_api` no longer fails to set up with
  "None is not a valid VacuumApi"; the map API is detected from the model.
- Ijai vacuums (ijai.vacuum.v1, v3, v17, v18, Xiaomi S10, S12, S20 and others on the Ijai map
  format) download and decrypt their map. Fixes "Padding is incorrect",
  `FailedMapDownloadException`, "Failed to get wifi_sn from vacuum" and "sequence item 0: expected
  str instance, NoneType found":
  - vacuums whose map already worked make the same requests and use the same key as before;
  - the map key type of the Mi Home plugin is tried, with `vacuum-map-parser-ijai` 0.1.1
    (upstream PR #725 by maksp86);
  - the standard map endpoint is tried when the "pro" one has no map (upstream PRs #725, #782 by
    Korkuttum and #787 by ave-tipsy);
  - WiFi serial numbers of 19 characters, with a slash, or reported as `null` are found, and are
    read through the Xiaomi cloud when Home Assistant cannot reach the vacuum (upstream PRs #782
    and #787);
  - maps encrypted with a device id of "0" to "9", or on a shared vacuum with the logged-in
    account, decrypt (upstream PRs #792 by Altar82, #785 by fixoid, #787 and #789 by
    odegaard12);
  - a map no key decrypts keeps the last map instead of making every entity unavailable, and a
    map uploaded while cleaning no longer fails on a missing map name (upstream PR #792);
  - the Xiaomi S12 asks for a fresh map while cleaning, so the map follows the vacuum (upstream
    PR #789);
  - the cleaned rooms sensor no longer logs an error with Ijai maps (upstream PR #786 by
    Korkuttum);
  - the vacuum's local token is no longer written to the debug log (upstream PR #787).
- Setup no longer aborts with "Unknown error occurred" when the Xiaomi cloud times out while listing
  the devices of one of your homes; that home is skipped.
- Re-authentication no longer fails with "Unknown error occurred" when the vacuum's IP address
  differs from the stored one (for example a setup imported from YAML). Picking a different vacuum
  during re-authentication now stops with a message instead of rewriting the setup.
- A Xiaomi outage (an error page or HTTP 5xx) no longer asks you to log in again or reports the
  vacuum as missing; the map update is retried, and a failed background login is not repeated
  every few seconds. Debug logs no longer contain session tokens, verification links or the
  signed map URL.

### Changed

- The integration is named **Better Xiaomi Cloud Map Extractor** (domain
  `better_xiaomi_cloud_map_extractor`), and bugs go to this fork's issue tracker.
