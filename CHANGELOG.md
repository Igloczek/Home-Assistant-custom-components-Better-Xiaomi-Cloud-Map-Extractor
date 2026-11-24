# Changelog

## Unreleased (0.1.0)

First release of this fork under its own name, based on upstream v3.0.0-alpha-24. It installs
next to the original as `better_xiaomi_cloud_map_extractor`.

### Added

- Import from Xiaomi Cloud Map Extractor: vacuums set up in the original integration move over
  with their settings, Xiaomi login, device and entity ids, so dashboards and automations keep
  working.

### Fixed

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
