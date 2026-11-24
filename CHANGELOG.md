# Changelog

## Unreleased (0.1.0)

First release of this fork under its own name, based on upstream v3.0.0-alpha-24. It installs
next to the original as `better_xiaomi_cloud_map_extractor`.

### Added

- Import from Xiaomi Cloud Map Extractor: vacuums set up in the original integration move over
  with their settings, Xiaomi login, device and entity ids, so dashboards and automations keep
  working. This includes vacuums the original imported from v2 YAML; one whose Xiaomi lookup
  failed back then asks to log in once after the import.

### Fixed

- A vacuum imported from v2 YAML without `force_api` no longer fails to set up with
  "None is not a valid VacuumApi"; the map API is detected from the model.
- Ijai vacuums (ijai.vacuum.v1, v3, v17, v18, Xiaomi S10, S12, S20 and others on the Ijai map
  format) download and decrypt their map. Fixes "Padding is incorrect",
  `FailedMapDownloadException`, "Failed to get wifi_sn from vacuum" and "sequence item 0: expected
  str instance, NoneType found":
  - the map key type and map endpoint follow the Mi Home plugin, with `vacuum-map-parser-ijai`
    0.1.1 (upstream PR #725 by maksp86);
  - the other map endpoint is tried when the first one has no map (upstream PRs #782 by
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
