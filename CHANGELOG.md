# Changelog

## Unreleased

First release of this fork under its own name, based on upstream v3.0.0-alpha-24. It installs
next to the original as `better_xiaomi_cloud_map_extractor`.

### Added

- Import from Xiaomi Cloud Map Extractor: vacuums set up in the original integration move over
  with their settings, Xiaomi login, device and entity ids, so dashboards and automations keep
  working.

### Fixed

- Setup no longer aborts with "Unknown error occurred" when the Xiaomi cloud times out while listing
  the devices of one of your homes; that home is skipped.

### Changed

- The integration is named **Better Xiaomi Cloud Map Extractor** (domain
  `better_xiaomi_cloud_map_extractor`), and bugs go to this fork's issue tracker.
