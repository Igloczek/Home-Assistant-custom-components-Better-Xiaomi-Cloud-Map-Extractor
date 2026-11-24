# Changelog

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
