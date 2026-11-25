# Changelog

## Unreleased

- Internal: split the cloud connector, config flow, repairs, v2 YAML import and map update into focused modules; no behaviour change.

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
