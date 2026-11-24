# Fork differences: sync preservation checklist

[`FORK.md`](../../../FORK.md) is authoritative. This file is the sync view: every active difference,
the files that implement it, and the proof that must still pass after an upstream import. An import
that merges cleanly but breaks a row here is a failed sync.

| Area (FORK.md anchor) | Protected implementation | Proof after the sync |
|---|---|---|
| [Project identity](../../../FORK.md#project-identity) | Folder and `const.py` `DOMAIN` (`better_xiaomi_cloud_map_extractor`), `manifest.json` (`domain`, `name`, `documentation`, `issue_tracker`, `codeowners`), `hacs.json` `name`, `brand/`, `README.md`, `.github/ISSUE_TEMPLATE/`, `translations/en.json` reauth text | An upstream file lands under `custom_components/better_xiaomi_cloud_map_extractor/`, never a recreated `xiaomi_cloud_map_extractor/` folder; links point at the fork |
| [Import from the original](../../../FORK.md#import-original) | `migration.py`; `__init__.py` `async_setup` and the `async_adopt_original_entities` call; `config_flow.py` import steps; `const.py` `ORIGINAL_DOMAIN`; import strings in `translations/en.json` | `tests/test_import_original.py` |
| [Test suite](../../../FORK.md#test-suite) | `tests/`, `requirements_test.txt`, `pyproject.toml` | `pytest` passes; no test was deleted or loosened |
| [CI and lint](../../../FORK.md#ci) | `.github/workflows/tests.yml`, `hacs.yaml`, `hassfest.yaml`; `pyproject.toml` ruff rules | `ruff check .`; no `automerge.yaml`, `lint_python.yml` or `dev` triggers come back |
| [Release packaging](../../../FORK.md#release-packaging) | `.github/workflows/release.yaml`, `hacs.json` | The workflow tests the commit and uploads `better_xiaomi_cloud_map_extractor.zip` with `manifest.json` at its root |
| [Device list timeout](../../../FORK.md#home-device-list-timeout) | `connector/xiaomi_cloud/connector.py` `_get_devices_from_home` | `tests/test_cloud_connector.py` |
| [Re-authentication target](../../../FORK.md#reauth-entry) | `config_flow.py` `async_step_confirm_data` (reauth and existing-entry lookup), `_unique_id_for`; `reauth_wrong_vacuum` in `translations/en.json` | `tests/test_config_flow.py` reauth and user-flow adoption tests |
