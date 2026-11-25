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
  remaining ones appear under **Discovered**, whether or not the original is installed and loaded.
  The import copies the entry's data, options and title and the stored cloud session
  (`.storage/xiaomi_cloud_map_extractor_<mac>`); no new login is needed. It then disables the
  original's entry (`disabled_by=user`) and waits for it to unload; the import step says so before
  the user confirms.
- **Entity ids.** At setup, before the platforms load, the original's entity registry entries for
  that vacuum move to this integration (`async_update_entity_platform`), then its device
  (`async_update_device`), so entity ids, names, areas, registry options and customisations stay and
  dashboards, automations and the map card keep working. Entities and the device that disabling the
  original marked `disabled_by=config_entry` are enabled again; entities disabled by the user or the
  integration stay disabled.
- **Only disabled, never deleted or edited.** Home Assistant refuses to uninstall the original while
  it has entries, and deleting an entry deletes the entities still linked to it, so the import
  cannot wait for the user to remove the original. It disables the entry instead, on the user's
  explicit confirm; the entry's data, options and storage file are never changed or deleted.
  Nothing is taken from an original entry that can still load it (enabled, with the original
  installed). A Repairs issue (`original_left_<entry_id>`, raised again at startup) then tells the
  user to delete the disabled entry and uninstall the original; it goes away once the entry is
  deleted or enabled. Deleting the entry removes only what is still linked to it, so nothing that
  moved is lost. When the original's v2 YAML block is still in the configuration, that issue puts
  removing it first, and a second issue shows the block ([Leftover original YAML](#original-yaml)).
- **Enabling the original again** takes nothing back: it sets the vacuum up again next to this
  integration with its own device and new entity ids (`…_2`), and this integration keeps working.
- **Which vacuum an entry is for** (`migration.vacuum_mac`): its unique id, or for entries the
  original imported from v2 YAML (source `import`, no unique id, titled "Xiaomi Cloud Map
  Extractor") the MAC in `data`. The imported entry gets that MAC as unique id, and the import list
  shows the host next to the title. A YAML-imported original without a MAC (its cloud lookup
  failed, so it never had entities) is matched by host: it is imported as it is, and setup then asks
  to log in once (reauth, one click with the stored credentials), which fills in the MAC, device id
  and unique id.
- **Legacy YAML** keeps working by changing `platform: xiaomi_cloud_map_extractor` to
  `platform: better_xiaomi_cloud_map_extractor`; it is then imported like upstream's and takes over
  the original's entities for the same MAC. It is skipped, without logging in, when the host or MAC
  is already set up here. Unlike upstream's, the entry gets the MAC as unique id.
- **Unique id at setup** (`migration.async_fill_unique_id`): an entry without a unique id but with a
  MAC in `data` (the YAML import of 0.1.0 and earlier) gets `format_mac(mac)` at setup, unless
  another entry (ignored ones included) already has it. Proof: `tests/test_entry_unique_id.py`.
- Proof: `tests/test_import_original.py`, run over every entry shape alpha-24 leaves
  (`tests/original_entries.py`: UI flow, YAML import, YAML import without MAC), with all 26
  registry entries, the device and the storage file; the original not installed, and installed and
  loaded (`install_original`, a stand-in that loads and unloads like alpha-24); discovery; legacy
  YAML; deleting the disabled entry; enabling it again.

## Versioning

The fork numbers its own releases, starting at `v0.1.0`, and carries only its own `vX.Y.Z` tags: the
51 inherited upstream tags (v1.x, v2.x, v3.0.0-alpha-N) were deleted on 2026-11-24, before any fork
release, and must not come back. Refer to upstream versions by commit SHA (v3.0.0-alpha-24 is
`688fe3e`); the tag rules are in [AGENTS.md](AGENTS.md#github-releases). 0.x releases are cut by agents once verified ([AGENTS.md](AGENTS.md#who-decides-what-ships));
1.0.0 is the maintainer's call against the [1.0.0 criteria](ROADMAP.md#100-criteria).

## Upstream snapshot

- Upstream baseline: `688fe3e` (`upstream/dev_extracted_libraries`, released upstream as v3.0.0-alpha-24), reviewed
  on 2026-11-24. Upstream's last push was 2026-02-20.
- Upstream `master` (v2) is not a source of imports; see **Branches**.
- Upstream open pull requests are imported one by one with `git cherry-pick -x` or as a reviewed
  re-implementation; [TRIAGE.md](TRIAGE.md) classifies them and [ROADMAP.md](ROADMAP.md) ranks them.
- Refresh this snapshot in the same change as an upstream import. Recompute the ahead/behind counts
  with `git log --left-right --cherry-pick --oneline upstream/dev_extracted_libraries...master`.

## Active differences

Each row is a preservation criterion for an upstream import. The
[sync checklist](.agents/skills/sync-upstream/fork-differences.md) maps every row to the files that
implement it and the proof that must still pass.

| Area | Behaviour in this fork | Why |
| --- | --- | --- |
| <a id="project-identity"></a>Project identity | The domain is `better_xiaomi_cloud_map_extractor` and the manifest and HACS name **Better Xiaomi Cloud Map Extractor**; `documentation`, `issue_tracker` and `codeowners` point at the fork; the README, issue forms and re-authentication text carry the fork's name, and `brand/icon.png` (256 px) and `brand/icon@2x.png` (512 px) give it its own icon, which Home Assistant 2026.3+ and HACS read from the integration folder. | The fork installs next to the original without a collision and has its own tracker. |
| <a id="import-original"></a>Import from the original | Original `xiaomi_cloud_map_extractor` entries are offered for import (menu and discovery); the entry, options and cloud session are copied, the original entry is disabled (never deleted or edited) on the user's confirm, and the device and entities move over with their ids, also while the original is installed. See **Compatibility**. | Switching must not cost users their setup, entity ids or dashboards, and Home Assistant only uninstalls the original once its entries are deleted. |
| <a id="test-suite"></a>Test suite | `tests/` runs on `pytest-homeassistant-custom-component` with the Xiaomi cloud and the vacuum mocked: the config flow (credentials, captcha, two-factor, several vacuums, duplicates, errors, reauth of every entry shape), setup and unload, and which connector errors start re-authentication. Every flow result and Repairs issue is checked against `translations/en.json` (`flow_results_render`, `issues_render`). `requirements_test.txt` pins the test harness and lists the integration's requirements. | Upstream has no tests. |
| <a id="ci"></a>CI and lint | `tests.yml` runs `ruff check .` (correctness rules, `pyproject.toml`) and `pytest` on Python 3.14 for pushes to and pull requests against `master`. HACS validation runs every check, including `issues` (the repository has HACS topics). `manifest.json` does not list `Pillow`, which Home Assistant provides and hassfest forbids in custom integrations, and asks for at least core's version of each `vacuum-map-parser-*` package that Home Assistant core also uses (`roborock>=0.1.5`), so it follows core's updates; the test requirements pin that version. There is no `dev` branch and no master→dev automerge job, and upstream's `lint_python` job (whose steps mostly ended in `|| true`) is replaced. | Upstream CI could not fail on a real problem, and the fork lands every change on `master`. |
| <a id="release-packaging"></a>Release packaging | `release.yaml` runs lint and tests on the released commit, sets the manifest `version` to the tag without its `v`, and attaches `better_xiaomi_cloud_map_extractor.zip` with the integration files at the archive root, as `hacs.json` (`zip_release`) expects. | A release must not ship a commit that fails the suite, and HACS installs from that zip. |
| <a id="home-device-list-timeout"></a>Device list timeout | A home whose device list times out or fails to connect is skipped (`_get_devices_from_home` catches the tuple `(FailedConnectionException, TimeoutError)`). | Upstream wrote `except A \| B`, which raises `TypeError` when an exception arrives, so one slow home aborted setup with "Unknown error". |
| <a id="reauth-entry"></a>Re-authentication target | Reauth updates the entry being re-authenticated (`self.context["entry_id"]`), whatever host is confirmed, and aborts with `reauth_wrong_vacuum` when the chosen vacuum's MAC differs from the entry's `unique_id` or stored MAC. The user flow decides duplicates by `unique_id` and adopts by host only an entry without `unique_id` (YAML import), which then gets the MAC as `unique_id`. | Upstream looked the entry up by the confirmed host, so reauth crashed with `AttributeError` ("Unknown error occurred") whenever the host differed, and the user flow could overwrite another vacuum's entry that had the same IP. |
| <a id="cloud-session-renewal"></a>Cloud session renewal | The `passToken` Xiaomi sets on every login (credentials, QR, two-factor) is stored with the session (`pass_token` in the connector store; older stores, including sessions imported from the original, load without it or with a UTC offset on the expiration). A session close to its stored expiration, or one the API rejects (401/403 or an `auth err` body), is renewed through `serviceLogin` with that `passToken` and the map download is retried; a password login runs only when Xiaomi refuses the `passToken`. A session that can't be renewed right now keeps being used until Xiaomi rejects it, and a refused proactive renewal is retried at most hourly. Every renewal or background login is written back to the store. | Upstream logged in with the password one day before the 30-day expiry and after every restart past it, and Xiaomi answers server-side password logins with a captcha or identity check, so the map stopped every month (upstream #697, PR #777). |
| <a id="network-errors-no-login"></a>Network errors never log in | Timeouts, connection errors and outage answers (HTTP 5xx, 408, 429, an HTML page or an undecodable body instead of Xiaomi's JSON) during login, renewal, device lookup and API calls raise `FailedConnectionException` (update retried), never a login error; only a JSON refusal from Xiaomi counts as a rejected password or `passToken`. Device lookup for the configured server no longer turns an error into "device not found". A password login runs on its own session and keeps the stored session and `passToken` until Xiaomi issues a new one; a failed one is not repeated for 15 minutes (`RetryLaterException`, update retried), and overlapping updates (refresh button and schedule) recover the session once. | A timeout or a 502 used to be reported as bad credentials or a missing device, which started reauth or a password login; a failed login wiped the session it was meant to replace, so every 10 s retry was another password login and Xiaomi answered with a captcha. |
| <a id="no-secrets-in-logs"></a>No secrets in the log or diagnostics | Login, captcha, two-factor, QR, renewal and map download log statuses and codes only: no `passToken`, `ssecurity`/`psecurity`, `serviceToken`, verification context or tickets, STS `clientSign`, response bodies or the signed map URL. Diagnostics redact (`async_redact_data`) the credentials, device token, host, MAC, name, title and unique id, the map decryption key (`enc_key`) and the signed map URL (`last_used_url`), and never include the stored session. Connection errors (`FailedConnectionException`, shown in the "Failed to update" message) drop every URL's query string, where aiohttp quotes the encrypted API's `ssecurity` and signature and the STS `clientSign`, and do not chain the original error into tracebacks. The Dreame parser's "Enc Key" debug line is redacted by a log filter in `vacuum_dreame.py`. | Users paste debug logs, diagnostics and error messages into public issues (upstream #778). |
| <a id="yaml-map-api"></a>Map API of YAML entries | An entry whose `used_map_api` is None (the v2 YAML import stores `force_api`, which is usually unset) uses the API detected from the model (`VacuumApi.configured`), in setup and in the entities' device info. A YAML `force_api` (lower case, `viomi`) is stored as the map format value (`VIOMI`), and entries imported with the lower-case spelling still load. | Upstream called `VacuumApi(None)`, so every YAML-imported vacuum without `force_api` failed to set up with `ValueError`, and one with `force_api` failed with `VacuumApi('viomi')`. |
| <a id="reauth-reason"></a>Re-authentication explains why | The coordinator keeps why reauth started (`AuthIssue`); the reauth flow opens with a captcha or account-verification step (with Xiaomi's verification link) when that was the cause, offers logging in again with the saved credentials, and the two-factor step shows the verification link and retries the login when submitted empty. `ConfigEntryAuthFailed` carries a translated message; captcha images, request headers and session tokens never reach the log. | The cause and the verification link were only in the log, next to a dump of the captcha image bytes. |
| <a id="xiaomi-map-key"></a>Xiaomi map key | The Xiaomi-format map key is derived from the last 16 characters of the model (`model[-16:]`, as the Xiaomi Home map plugin does with `Device.model.slice(-16)`), for decryption and the `enc_key` attribute. | Upstream used `model.replace("xiaomi", "mi")`, which is 16 bytes only for 20-character models; `xiaomi.vacuum.c107`, `d101`, `b112` and other short names failed with "Incorrect AES key length" (upstream #732, #733). |
| <a id="update-cycle"></a>Update cycle | No python-miio call or map decoding/drawing runs on the event loop (`asyncio.to_thread`, upstream PR #775 adapted: `should_update_map` is async). `MiotDevice` gets `mapping={}`. `should_update_map` returns `None` when the vacuum cannot tell whether it moves (no status, a status that is not a number, Ijai unreachable on the LAN); the connector then downloads every update only while the map changes and once per `UNCHANGED_MAP_CHECK_INTERVAL` after `UNCHANGED_MAP_DOWNLOADS` identical downloads. A missing map is always retried. `get_data` returns a copy whose equality ignores timestamps and `map_data`, and the coordinator runs with `always_update=False`, so an unchanged update writes no state; the auto-update switch writes its own state. Tests run with Home Assistant's blocking-call detection on (`tests/conftest.py`). | Upstream blocked the event loop on every update (upstream #775, #769), logged a mapping warning at every start (#791), downloaded a docked vacuum's map every 10 s forever (#764) and wrote a new state every update (#719). |
| <a id="cloud-run-state"></a>Run state through the cloud | Viomi, Roidmi and Dreame models with a python-miio status mapping (`base/cloud_run_state.py`: Viomi v6/v7/v8/v10/v13 miIO `get_prop` `run_state`; Roidmi v60 MIoT 2/1; Dreame mc1808 3/2 and p2008/p2009/p2028/p2041o/p2150a/p2150o 2/1) read their run state on every update with one `/v2/home/rpc/<did>` call through the stored cloud session. `should_update_map` returns True while it moves and False after `OFF_UPDATES` idle reads; any failure or a value that is not a number returns None (the once-a-minute map check), never a failed update. Other models of these vendors are unchanged. `lan_reachable` stays None (no LAN call). | Upstream had no status for these vendors, so the fork's unchanged-map check followed a starting clean up to 60 s late; the cloud reaches the vacuum where the LAN may not. |
| <a id="ijai-maps"></a>Ijai maps | `vacuum_ijai.py` combines upstream PRs #725 (parser 0.1.1: K3 map endpoint and hex key type), #782, #787, #792, #785, #789 and #786, none merged upstream. What worked on v3.0.0-alpha-24 is tried first and unchanged: the "pro" map endpoint, its WiFi serial rules and its key (the full MD5 digest for every model, as parser 0.1.0 did). After it: the standard endpoint; serials with 19 characters, a slash or a `null` property, read through the cloud (`get_properties`) when the vacuum is not reachable locally; the short key that parser 0.1.1 uses for models outside the Mi Home plugin's hex list; device ids "0" to "9" and the logged-in account's uid, remembering the key that worked; an undecryptable map raises `FailedMapParseException`, or `MapSavingDisabledException` (see [map saving off](#ijai-map-saving)) when the vacuum reports map saving off; a current map missing from `mapInfo` is added before parsing; `xiaomi.vacuum.b106eu` requests a real-time upload; `as_list_of_dict` passes plain ids through. Not taken from #782: upload requests for every model, map caching and download retries. Not taken from #785: the coordinator catching every exception. | Ijai was the largest broken group on v3; the upstream fixes were never merged. |
| <a id="wrong-map-format"></a>Wrong map format repair | When a vacuum set to the Xiaomi map format fails to parse and its model belongs to a vendor with its own format (`VacuumApi.detect`), a fixable Repairs issue offers that format; confirming stores it in `used_map_api` and reloads the entry. The issue goes away when the map works and when the entry is removed. Nothing is refused by model name. | Roborock users on the Xiaomi format (upstream #731, #763, #784) never got a map and only the log said why; the fix was deleting and re-adding the vacuum. |
| <a id="qr-login"></a>QR login | The QR step keeps its placeholders while Home Assistant re-runs the progress step, ends only through `async_show_progress_done`, and every failure lands on the `auth_qr_failed` form with a specific error (`qr_timeout`, `qr_rejected`, `cannot_connect`, `qr_code_unavailable`, `qr_failed` for a bug, logged) whose submit shows a new code; it works in reauth too. The long poll is asked again until the code's `timeout` (default 5 min, at most 15) runs out; outages and undecodable answers raise `FailedConnectionException`, refusals `FailedLoginException`. The session keeps `cUserId`, falls back to the default lifetime without a `userId` cookie and fails cleanly without a `serviceToken`. The login link drops the `_json=true` that `loginUrl`'s redirect carries (resolved with a 15 s timeout, network errors only; the original link otherwise), keeping `callback` and `followup`. Upstream PRs #796, #795 and #766, none merged upstream. | On Home Assistant 2026.9+ the flow crashed after the login was approved ("Show progress can only transition…", upstream #779), the frontend showed `MISSING_VALUE` instead of the code, a session saved without `cUserId` lost the device list after a restart, and a signed-out browser got a JSON document from the link. |
| <a id="persistent-failure-repairs"></a>Repairs for persistent failures | `issues.py` counts update outcomes per entry and raises a fixable issue when the same failure repeats: `device_not_found`, `vacuum_token` (the vacuum refuses its token, Roborock or MiOT) and `map_download_failing` after `FAILURE_THRESHOLD` updates in a row, cleared by the next good map; `vacuum_unreachable` after `LAN_UNREACHABLE_UPDATES` updates in which the vacuum did not answer on the LAN (`lan_reachable`, set by the vendors that read their status locally: Roborock, Ijai, Xiaomi), cleared when it answers. The fix flow (`repairs.py`) looks the vacuum up in every region with the stored session (renewing it with the `passToken`), matches it by device id or MAC, and offers the region, IP address and token the cloud lists; token and LAN issues offer the form with the stored values when the cloud is down. Reauth starts only when the session can't be renewed or the account lacks the vacuum. `DeviceNotFoundException` and `InvalidDeviceTokenException` no longer start reauth. The issues are deleted with the entry. | Upstream started a reauth for a region or token problem, which a new login does not fix, and a vacuum with a new IP address only showed up in the debug log. |
| <a id="ijai-map-saving"></a>Ijai: map saving off | When an Ijai map fails to download or decrypt, `vacuum_ijai.py` reads `remember-state` (siid 10, piid 1; 0 Close, 1 Open) through the cloud (`get_properties`). That is the map saving switch in the miot-spec of every `ijai.vacuum` model and of `xiaomi.vacuum.b106eu`, `c103` and `d106gl`. When it reads 0, `MapSavingDisabledException` replaces the generic failure. Any other answer, or a failed read, keeps the original failure. The connector keeps the last map with status `MAP_SAVING_DISABLED`, and re-raises when it has no map, so the update (or setup) fails with the translated message `map_saving_disabled`. `issues.py` raises the non-fixable issue `map_saving_disabled` after `FAILURE_THRESHOLD` updates, instead of `map_download_failing`, and clears it with the next good map. The Xiaomi-format models have no such property in their specs, so they are not covered. | Upstream #759: with map saving off the map fails with "Padding is incorrect", and only turning it on in Xiaomi Home fixes it. Nothing in the log said so. |
| <a id="dreame-encrypted-map"></a>Dreame: encrypted map without a known IV | `vacuum_dreame.py` `decode_and_parse` wraps vacuum-map-parser-dreame. When unpacking fails (zlib, base64, `ValueError`) on a model missing from `DreameMapDataParser.IVs` and the payload is whole AES blocks without a zlib header (`_looks_encrypted`), it raises `EncryptedMapUnsupportedException(model)`. Any other unpack or parse failure (`NotImplementedError` for a P-frame, bad JSON, a short header) raises `FailedMapParseException`. The connector keeps the last map with status `MAP_ENCRYPTED` and re-raises when it has none, so the update (or setup) fails with the translated message `map_encrypted`. `issues.py` raises the non-fixable issue `map_encrypted` after `FAILURE_THRESHOLD` updates. The issue names the model and links the fork's issue tracker (`ISSUES_URL`, placeholders `model` and `issues_url`), and clears with the next good map. | Upstream #450, #757, #772: dreame.vacuum.md1808 serves an AES-CBC encrypted map (the #450 attachment: standard base64, 5136 bytes, whole blocks, no zlib header, no known key or IV opens it). Its IV is in no public table, so the map went into zlib as is, and "incorrect header check" reached users as an unknown error. |
| <a id="original-yaml"></a>Leftover original YAML | At startup, `async_setup` keeps every `camera` item with `platform: xiaomi_cloud_map_extractor` from the configuration Home Assistant passes it, with the file and line it was loaded from (also `!include` files and packages). When one is for a vacuum set up here (same host or token), a Repairs issue (`original_yaml_<entry_id>`, raised at every setup of the entry, gone with the entry or a restart without the block) names the file and line, shows the block with username, password and token redacted, and says removing it is safe. While the original's entry is still left, its `original_left` issue becomes `original_left_yaml`, which lists removing the block before deleting the entry and uninstalling the original. configuration.yaml is never edited. | v2 users keep that block after the import; once the original is uninstalled Home Assistant logs "Platform error: camera - Integration 'xiaomi_cloud_map_extractor' not found" at every start, and core raises no repair for a missing platform. |
| <a id="actionable-failures"></a>Actionable failures | No config flow step aborts with "Unknown error": login errors become form errors (`cannot_connect` for outages, throttling and raw network errors; `cloud_login_error` for refusals; `unexpected_response`, logged, for anything else), and a device list that fails after the login opens `devices_failed`, whose submit retries it (or returns to the login choice when Xiaomi refused the session). Listing devices on every region skips failing regions only while another one lists devices; otherwise the failure is raised, and a home answer without `result` is an outage. The coordinator turns every exception into `ConfigEntryAuthFailed` or an `UpdateFailed` with a translation key (`cloud_unavailable`, `login_retry_later`, `map_download_failed`, `map_parse_failed`, `vacuum_unreachable`, `unexpected_error`). The YAML import catches `Exception`, not `BaseException`. Setup's last step shows `session_not_saved` and retries when the session can't be written to the store. | Upstream aborted with "Unknown error" for any unexpected login or device-list error, reported a Xiaomi outage as "no vacuums" or a wrong password, and let non-Xiaomi exceptions escape the coordinator. |
| <a id="ijai-fit-explored"></a>Ijai: fit to the explored area | Upstream PR #794, adapted, behind an option. Ijai entries (by `VacuumApi.configured`) get **Fit to the explored area** (`fit_to_explored` in the `image_config` options, off by default; entries without the key, such as every entry written by v3.0.0-alpha-24 or imported from the original, render as before). When on, `ijai_render.py` crops each map to its explored pixels plus 15 source pixels and zooms it to at least 960 px on the longer edge; a configured trim or scale that cuts or enlarges more wins. Sizes and the room-name font (Pillow's default, `load_default(size=10 × zoom)`) grow by the same zoom (fitted scale ÷ configured scale), so the result is the full map enlarged. A map with nothing explored is drawn whole. Saving the image options keeps keys the form does not show. Not taken from #794: the bundled DejaVu Sans font (about 760 KB), the transparent background (the "map outside" color option does that) and sizing icons to a share of the image width. | Ijai vacuums upload a fixed canvas (800x800) however little is explored, so the floor plan is a small island with tiny icons; turning it on changes how existing maps look, so it is the user's choice. |
| <a id="no-scripts"></a>No offline map script | `scripts/` (`map_processor.py` and its README) is removed, with its ruff exclusion. | It has not run since upstream's 2023 parser extraction (it imports `VacuumCamera` and `vacuum_platforms`, gone on v3) and was never in the release zip; upstream PR #591 only fixed its README. |
| <a id="roidmi-pro-map"></a>Roidmi sdj60 map | `vacuum_roidmi.py`: for the models in `PRO_MAP_MODELS` (`roidmi.vacuum.sdj60`) the map URL comes from `get_interim_file_url_pro` first, then `get_interim_file_url` (each asked once), and `RoidmiProMapDataParser` takes the bitmap as the `width * height` bytes right before the first `{"` (the JSON), instead of vacuum-map-parser-roidmi's split at byte 16 and the first outside cell (127) before `{`. The rest of its `parse` is the library's; a map shorter than its bitmap fails to parse. Other Roidmi-format models keep the standard endpoint first and the library parser. `base/vacuum_v2.py` `get_interim_file_url` asks one endpoint (same requests as before for every vendor). Upstream PR #768 (v2), not merged upstream. | For the sdj60, `get_interim_file_url` points at a file that is no map (upstream #572: "list indices must be integers"), and its pro map does not end the bitmap with an outside cell. |

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
git config remote.upstream.tagOpt --no-tags
git config remote.origin.tagOpt --no-tags
```

Follow [the sync skill](.agents/skills/sync-upstream/SKILL.md) for the procedure.
