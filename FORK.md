# Fork scope and upstream differences

This repository is Igloczek's hard fork of
[PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor),
published as **Better Xiaomi Cloud Map Extractor**. Upstream's v3 line (`dev_extracted_libraries`,
released as v3.0.0-alpha-1 to alpha-24) is the baseline; this file records where the fork behaves
differently and how to import upstream changes. Planned work is in [ROADMAP.md](ROADMAP.md),
released changes in [CHANGELOG.md](CHANGELOG.md).

## Branches

- `master` is the fork's only development branch. It started at upstream `dev_extracted_libraries`
  `688fe3e` (v3.0.0-alpha-24).
- `v2` keeps upstream's legacy v2 line (upstream `master` at `62285a7`, v2.2.5 plus three
  commits). It is frozen: no fixes or releases. Its three commits after the common base are covered
  on `master`: the user-agent changes (`410ba42`, `62285a7`) are already in v3's
  `connector/xiaomi_cloud/utils.py`, and `43b20eb` only edited upstream's donation links.

## Compatibility

The fork has its own domain, `better_xiaomi_cloud_map_extractor` (folder
`custom_components/better_xiaomi_cloud_map_extractor`, release asset
`better_xiaomi_cloud_map_extractor.zip`, storage `.storage/better_xiaomi_cloud_map_extractor_<mac>`),
so it installs next to the original `xiaomi_cloud_map_extractor` without either overwriting the
other. Config entry data and options keys, entity unique ids (`<device_id>_<entity domain>_<key>`)
and entity names are the same as upstream's v3.0.0-alpha-24.

Switching keeps a user's setup (`migration.py`):

- **Import.** When `xiaomi_cloud_map_extractor` config entries exist that are not set up here, the
  setup menu offers **Import from Xiaomi Cloud Map Extractor**, and once the integration runs, the
  remaining ones appear under **Discovered**. The import copies the entry's data, options and title
  and the stored cloud session (`.storage/xiaomi_cloud_map_extractor_<mac>`); no new login is
  needed.
- **Entity ids.** At setup, before the platforms load, the original's entity registry entries for
  that vacuum move to this integration (`async_update_entity_platform`), then its device
  (`async_update_device`), so entity ids, names, areas and customisations stay and dashboards,
  automations and the map card keep working.
- **The original stays untouched while it can run.** Nothing is imported or moved while the
  original integration is installed (`async_get_integration` finds it): the import shows "remove
  Xiaomi Cloud Map Extractor in HACS and restart" and changes nothing. The original config entry and
  its storage file are never modified or deleted; the user deletes the leftover entry afterwards.
- **Not imported:** original entries without a unique id (v2 YAML imports whose cloud lookup
  failed). Legacy YAML keeps working by changing `platform: xiaomi_cloud_map_extractor` to
  `platform: better_xiaomi_cloud_map_extractor`; it is then imported like upstream's.
- Proof: `tests/test_import_original.py` (an alpha-24 entry, registry entries, device and storage
  file; original installed and not installed; discovery; removing the leftover entry).

## Versioning

The fork numbers its own releases, starting at `v0.1.0`. Upstream's `v3.0.0-alpha-N` tags are
history. 0.x releases are cut by agents once verified ([AGENTS.md](AGENTS.md#who-decides-what-ships));
1.0.0 is the maintainer's call against the [1.0.0 criteria](ROADMAP.md#100-criteria).

## Upstream snapshot

- Upstream baseline: `688fe3e` (`upstream/dev_extracted_libraries`, tag `v3.0.0-alpha-24`), reviewed
  on 2026-11-24. Upstream's last push was 2026-02-20.
- Upstream `master` (v2) is not a source of imports; see **Branches**.
- Upstream open pull requests are imported one by one with `git cherry-pick -x` or as a reviewed
  re-implementation; the triage that ranks them is in [ROADMAP.md](ROADMAP.md).
- Refresh this snapshot in the same change as an upstream import. Recompute the ahead/behind counts
  with `git log --left-right --cherry-pick --oneline upstream/dev_extracted_libraries...master`.

## Active differences

Each row is a preservation criterion for an upstream import. The
[sync checklist](.agents/skills/sync-upstream/fork-differences.md) maps every row to the files that
implement it and the proof that must still pass.

| Area | Behaviour in this fork | Why |
| --- | --- | --- |
| <a id="project-identity"></a>Project identity | The domain is `better_xiaomi_cloud_map_extractor` and the manifest and HACS name **Better Xiaomi Cloud Map Extractor**; `documentation`, `issue_tracker` and `codeowners` point at the fork; the README, issue forms and re-authentication text carry the fork's name, and `brand/icon.png` (256 px) and `brand/icon@2x.png` (512 px) give it its own icon, which Home Assistant 2026.3+ and HACS read from the integration folder. | The fork installs next to the original without a collision and has its own tracker. |
| <a id="import-original"></a>Import from the original | Original `xiaomi_cloud_map_extractor` entries are offered for import (menu and discovery); the entry, options and cloud session are copied and the device and entities move over with their ids, never while the original is installed. See **Compatibility**. | Switching must not cost users their setup, entity ids or dashboards. |
| <a id="test-suite"></a>Test suite | `tests/` runs on `pytest-homeassistant-custom-component` with the Xiaomi cloud and the vacuum mocked: the config flow (credentials, captcha, two-factor, several vacuums, duplicates, errors), setup and unload, and which connector errors start re-authentication. `requirements_test.txt` pins the test harness and lists the integration's requirements. | Upstream has no tests. |
| <a id="ci"></a>CI and lint | `tests.yml` runs `ruff check .` (correctness rules, `pyproject.toml`) and `pytest` on Python 3.14 for pushes to and pull requests against `master`. HACS validation runs every check, including `issues` (the repository has HACS topics). `manifest.json` does not list `Pillow`, which Home Assistant provides and hassfest forbids in custom integrations, and asks for at least core's version of each `vacuum-map-parser-*` package that Home Assistant core also uses (`roborock>=0.1.5`), so it follows core's updates; the test requirements pin that version. There is no `dev` branch and no master→dev automerge job, and upstream's `lint_python` job (whose steps mostly ended in `|| true`) is replaced. | Upstream CI could not fail on a real problem, and the fork lands every change on `master`. |
| <a id="release-packaging"></a>Release packaging | `release.yaml` runs lint and tests on the released commit, sets the manifest `version` to the tag without its `v`, and attaches `better_xiaomi_cloud_map_extractor.zip` with the integration files at the archive root, as `hacs.json` (`zip_release`) expects. | A release must not ship a commit that fails the suite, and HACS installs from that zip. |
| <a id="home-device-list-timeout"></a>Device list timeout | A home whose device list times out or fails to connect is skipped (`_get_devices_from_home` catches the tuple `(FailedConnectionException, TimeoutError)`). | Upstream wrote `except A \| B`, which raises `TypeError` when an exception arrives, so one slow home aborted setup with "Unknown error". |

## Upstream sync preservation contract

Every row in **Active differences** is a preservation criterion for an upstream import, including
changes that merge without conflicts. Before importing, follow
[.agents/skills/sync-upstream/SKILL.md](.agents/skills/sync-upstream/SKILL.md).

- Reconcile upstream improvements at the affected boundary; do not freeze an old implementation.
- Never resolve a conflict by choosing one side wholesale. Keep the fork behaviour and reapply the
  upstream change around it.
- Never delete a fork test, a FORK.md row, a documentation anchor or the sync skill to make an import
  pass.
- If upstream now provides the same behaviour, prove equivalence with the tests, then move the row
  to **Superseded differences** and record the upstream commit.

## Superseded differences

None yet.

## Scope boundaries

- Keep the fork focused on concrete bugs and features with a Home Assistant use case.
- A vendor-specific fix needs a fixture (a captured, scrubbed map or cloud response) or a report from
  someone with the device; see the pre-release rule in [AGENTS.md](AGENTS.md).
- Prefer small modules for fork-only logic so upstream imports touch fewer lines.
- When a new intentional difference is added, record it here with what it changes and why, and add it
  to the sync checklist.

## Importing from upstream

This checkout has an `upstream` remote. In a fresh clone:

```sh
git remote add upstream https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor.git
```

Follow [the sync skill](.agents/skills/sync-upstream/SKILL.md) for the procedure.
