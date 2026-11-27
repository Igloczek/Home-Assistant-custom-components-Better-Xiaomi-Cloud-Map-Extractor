# Agent guide

## North star and autonomy

A Home Assistant user's vacuum map just keeps working, and when it can't, Home Assistant tells them exactly what to do, in one click. Nobody ever has to read logs.

This is a Home Assistant custom integration, not a nuclear plant: in the worst case someone checks their vacuum in the Xiaomi Home app or rolls back a version. The maintainer does not know every vacuum model, does not own every device or region, and does not want to study every routine user request.

**Default to 99% automation.** Agents and the **Chief of staff** own decisions, implementation, pull request review, merging and releases on their own — without asking the maintainer — as long as a change:

1. **is not breaking** (existing config entries, entity IDs, dashboards, migrations and workflows keep working unchanged),
2. **does not drastically change UX** for existing users (opt-in options or clear, non-disruptive refinements), and
3. **cannot make the overall experience or quality worse** (proven by red-first tests and full local checks; no brittle hacks, leaked secrets or bloat).

Priorities, in order:

1. **Reliability** of the cloud session and the map download.
2. **Every failure actionable in the UI:** re-authentication and repairs, clear messages, never "Unknown error".
3. **Drop-in from the original:** the new domain installs side by side, and the import keeps entity ids and dashboards.
4. **Clear the upstream backlog.**
5. **Test-first, CI green.**

When two pieces of work compete, the one higher on this list goes first. [`ROADMAP.md`](ROADMAP.md) is ranked by it.

## Chief of staff

A standing pinned thread titled `Chief of staff` owns day-to-day delivery for this repository, backed by a daily scheduled sweep (`Chief of staff (daily sweep)`). It keeps `master` CI green (checking after every push and release and never treating a red check as harmless), reviews and merges open pull requests, decides on feature and follow-up proposals, launches and steers `Build #N` threads (without waiting idly on silent reporters when a problem can already be investigated or proven locally), ships confirmed fixes immediately instead of stalling for every other model, follows up on pre-release beta feedback, revives any failed triage runs, cuts batched stable releases with user-friendly notes, keeps `ROADMAP.md` current, closes fixed issues with clear explanations, and settles completed threads.

- Triage and build threads hand ready pull requests, reporter beta confirmations and design calls to `Chief of staff` (`t3_thread_search` for `Chief of staff`), not to the maintainer.
- `Chief of staff` only escalates to the maintainer for the ~1% calls under "Who decides what ships". Routine merges, opt-in options and releases do not ask for approval.

## Fork relationship

- This repository is Igloczek's hard fork of `PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor`, published as **Better Xiaomi Cloud Map Extractor**. Upstream is abandoned; its v3 line (`dev_extracted_libraries` at `688fe3e`, released as v3.0.0-alpha-24) is the baseline, and the legacy v2 line is frozen on the `v2` branch.
- [`FORK.md`](FORK.md) holds the compatibility promise, the differences from upstream and the import rules. Read it before changing fork-specific behaviour or importing upstream code. Every row under **Active differences** is a preservation criterion.
- Add every intentional fork-only feature or fix to `FORK.md` with what it changes and why, and add it to the [sync checklist](.agents/skills/sync-upstream/fork-differences.md).
- [`ROADMAP.md`](ROADMAP.md) holds planned work and postponed items, [`TRIAGE.md`](TRIAGE.md) the classification of every open upstream issue and pull request; [`CHANGELOG.md`](CHANGELOG.md) holds released changes. Do not keep a record of completed work in a standing document.
- To import an upstream commit or open upstream pull request, follow [`.agents/skills/sync-upstream/SKILL.md`](.agents/skills/sync-upstream/SKILL.md). Never push to `upstream` and never comment on upstream issues or pull requests.
- The issue tracker is triaged by the issue triage agent (`Igloczek/issue-triage-agent`, private): a T3 webhook task on the maintainer's Mac runs its procedure for each new issue or comment, one thread per issue. What it needs to know about this project is in [`.github/triage.md`](.github/triage.md), and this project's end-to-end cases are in `.github/triage-e2e/`. No workflow comments on or edits issues; `.github/workflows/triage-e2e-file.yml` only files `triage-test` issues for that project's end-to-end test.

## Compatibility

The fork's domain is `better_xiaomi_cloud_map_extractor`, so it installs next to the original `xiaomi_cloud_map_extractor`. Users switch with the import in `migration.py` (FORK.md "Compatibility"), which keeps their entity ids. Keep upstream's config entry keys and entity unique ids (`<device_id>_<entity domain>_<key>`): the import relies on them. A change that needs a new key adds a config entry migration (`VERSION`/`MINOR_VERSION` and `async_migrate_entry`) with a test that loads an entry written by v3.0.0-alpha-24 (`688fe3e`). Never delete the original integration's entries or files, and never change their data or options. The one exception: the import may disable (`disabled_by=user`, reversible) the original's entry for the vacuum being imported, only after the user confirms the step that says so, and then move that entry's device and entities. Never take anything from an original entry that can still load it (enabled, with the original installed).

## Project map

- Home Assistant custom integration in `custom_components/better_xiaomi_cloud_map_extractor/`.
- Setup and config: `__init__.py` (entry → `XiaomiCloudMapExtractorConnectorConfiguration`), `options_flow.py`.
- Config flow: `config_flow.py` registers the one handler, composed of the step modules in `flow/`: `login.py` (menu, credentials, captcha, two-factor, QR), `reauth.py`, `original.py` (import from the original integration and from YAML), `vacuum.py` (device list, vacuum selection, `confirm_data`, entry creation), `errors.py` (form error for a failure), `base.py` (state the steps share). Moved code keeps logging under the `config_flow` logger.
- v2 YAML: `legacy_schema.py` (keys and schema), `legacy.py` (deprecation issue and conversion into an entry).
- Repairs: `issues.py` counts update outcomes and raises or clears issues (logs under `repairs`); `repairs.py` holds their fix flows. The original's leftover v2 YAML: `original_yaml.py` (instruction-only issue; never edits user YAML files).
- Map update: `connector/__init__.py` (`XiaomiCloudMapExtractorConnector`: when to download, map status), `connector/cloud_session.py` (keeps the cloud session working: passToken renewal, throttled password login, storing new sessions), `connector/platforms.py` (map format → vendor class).
- Runtime: `coordinator.py` maps connector exceptions to `ConfigEntryAuthFailed` (reauth) or `UpdateFailed` (retry); entities in `image.py`, `camera.py`, `sensor.py`, `binary_sensor.py`, `button.py`, `switch.py`.
- Cloud access: `connector/xiaomi_cloud/connector.py` (`XiaomiCloudConnector`, the one object callers use, `to_config`/`from_config`) composed of `session.py` (session data, stored config, `_login_attempt`), `login_credentials.py` (password login, captcha), `login_two_factor.py` (identity verification), `login_qr.py`, `renewal.py` (passToken), `devices.py` (homes and devices per region), `api.py` (RC4 encrypted calls, map download) and `network.py` (outage versus refusal). All log under the `connector.xiaomi_cloud.connector` logger. The session is persisted per MAC by `store.py`.
- Vendors: `connector/vacuums/vacuum_*.py` download the map; parsing and drawing live in the external `vacuum-map-parser-*` packages pinned in `manifest.json`.
- Agent problem reporting: [`.agents/skills/report-problem/SKILL.md`](.agents/skills/report-problem/SKILL.md) (procedure for users' AI agents), `scripts/agent-report/` (zero-dependency Python 3 collector, redactor, renderer and validator, tested by `tests/test_agent_report.py`), [`docs/report-a-problem.md`](docs/report-a-problem.md) and `.github/ISSUE_TEMPLATE/agent_report.yml`.

## Fixes are for every user

- Fix problems in the integration, for every supported vacuum and region. A report from one user's vacuum is a sample of a general bug: find the general cause and check the other vendors and flows for it.
- Never create or change scripts, automations, helpers or Home Assistant configuration in a user's Home Assistant (including the maintainer's `ha.igielski.dev`) to work around an integration bug.
- When a vacuum or cloud protocol cannot do what a feature needs, say so and surface it clearly in the UI or document the limitation; do not ship a per-installation workaround as the fix.

## Local checks

```sh
uv venv -p 3.14 .venv && uv pip install -p .venv -r requirements_test.txt   # once
.venv/bin/ruff check .
.venv/bin/pytest
```

- Tests use `pytest-homeassistant-custom-component`; the Xiaomi cloud and the vacuum are always mocked (`tests/conftest.py`). Never put a real account, token, IP or map in a fixture; scrub captured data first.
- Keep `requirements_test.txt` in step with `manifest.json` requirements; bump the pinned harness to follow Home Assistant releases.
- Run the smallest check that proves a change (one test file or `-k`), then the full `pytest` before pushing. It takes a few seconds.
- CI (`.github/workflows/tests.yml`, `hacs.yaml`, `hassfest.yaml`) runs on every push and pull request to `master`. `pyproject.toml` enforces Home Assistant Core's correctness, async, logging, deprecation and Bandit security Ruff rules (`S102`..`S609`, `ASYNC`, `G`, `LOG`, `RUF012`, `TRY400`, etc.). For deeper Home Assistant architecture audits, consult `home-assistant/core`'s official skills (`ha-integration-knowledge`, `ha-review`, `ha-quality-scale-verify`) and `pylint-home-assistant`.
- When a GitHub UI action is blocked by API permissions (such as re-requesting a review on an external repository), use the built-in browser (`preview_*`, already logged into GitHub) directly instead of asking the maintainer to click it.

## Every bug improves the test suite

A bug that reached a user is also a bug in the test suite: the suite let it through. Fix both, so the suite grows with every bug and the same class of problem cannot come back.

- **Red first.** Before fixing, write a test that reproduces the bug and fails on the current code. Run it against the unfixed code and see it fail, then fix and see it pass. A test that was never red proves nothing.
- **Find the gap, not only the case.** Ask why the suite missed it and close that gap for the whole class: a mock that answers more politely than the real Xiaomi cloud, a flow step never driven to the end, a config entry shape only older versions write, a vendor map never fed through the parser. Extend the fixtures and helpers so every later test gets the coverage too.
- **Test where the bug lives.** Flow and setup behaviour through Home Assistant (`hass.config_entries.flow`, `async_setup`); cloud protocol handling at the connector with recorded, scrubbed responses; map parsing with a captured map file. A bug that crossed levels gets a test at each.
- **Keep fixtures real.** When a user's report shows a response, model, or map the fixtures lack, add a scrubbed copy under `tests/fixtures/` so it stays covered.
- **Report what testing turns up.** Fix extra problems when they are in scope; otherwise name them to the maintainer instead of fixing them silently or leaving them unmentioned.
- **Never weaken the suite to pass.** Do not delete, loosen or skip a test to make a change pass. A flaky test is a bug of its own.
- In the hand-off or report, name the test that was red before the fix and the gap in the suite it closes.

## Who decides what ships

Agents drive this project end to end: they investigate, reproduce, decide option names and shapes, build, test, review, merge and release without waiting to be asked. Sort every change into one of these three buckets:

1. **Agents decide, merge and ship (~99% of work).** Bug fixes, additive opt-in options, vendor/protocol fixes proven locally, Repairs improvements, documentation, and non-disruptive UX refinements. Pick the cleanest design yourself, prove it with red-first tests and the checks in "Local checks" on the exact head, and merge — directly for a clear bug, or via `Chief of staff` review for new options and refinements — without asking the maintainer, as long as the change is **not breaking**, does **not drastically change UX**, and **cannot make overall experience or quality worse**.
2. **Needs real-hardware confirmation first: pre-release.** Use this only when a change genuinely depends on a vacuum model, region or account state we cannot reproduce locally. Keep the pull request open, publish a `vX.Y.Z-beta.N` pre-release from its head ("GitHub releases"), and ask the reporter's AI agent to test it. Once they confirm it works, `Chief of staff` merges and ships the stable release autonomously without asking the maintainer.
3. **Escalate to the maintainer (~1% of work).** Build or propose it, then stop and ask the maintainer in chat (never on GitHub) **only** when a change:
   - is **breaking** (removes or renames config keys/entities, breaks compatibility or migrations, or changes defaults in a way that breaks existing setups),
   - **drastically changes UX** (a major config-flow/visual overhaul or changing how the integration works for everyone), or
   - **risks degrading overall experience or quality** (high maintenance burden, risky parser dependency upgrades with breaking signatures, or changing the fork's compatibility promise or scope).

**GitHub comment disclosure.** Any comment posted autonomously on GitHub by any agent (triage, build, `Chief of staff`, pre-release instructions, or release notices — anything posted via the maintainer's account) must always start with:

```markdown
<!-- triage-agent -->
> 🤖 **AI agent reply.** This is automated delivery, not the maintainer. They may check this issue later.
```

(Triage runs may say `automated triage` instead of `automated delivery`.) Never post an unmarked GitHub comment as an agent: both users and future triage runs must always be able to tell an agent message apart from the maintainer.

**What an escalation hand-off contains.** Keep it in the chat with the maintainer, never on GitHub: do not post hand-offs, screenshots or summaries as pull request or issue comments, and keep pull request descriptions short. GitHub comments are only for external contributors (the pre-release flow). Present it with the chat's visualization tools: the problem and the change in a short paragraph written for a user; what it looks like (config flow screens, rendered map) before and after when visual; the risk or trade-off that triggered escalation, alternatives and recommendation; what is proven and what is not. End with one question the maintainer can answer yes or no, or by picking an option.

**Releases are batched.** Merging does not mean releasing. Before cutting a release, `Chief of staff` checks other running agent threads, open pull requests close to merging, and what `master` gained since the last tag (`git log <last tag>..origin/master`). Cut a release autonomously when a batch is ready, not after every merge. Release on its own only for urgent breakage: setup that fails for users, lost configuration, or leaked credentials. Only changes escalated under bucket 3 wait for maintainer approval before merging and releasing. Once a fix is released in a stable version, close its tracking issue as `completed` instead of keeping it open — always with a short, clear comment (marked with the AI agent reply header) stating what version fixed it, what was verified, and that replying with a problem on that version will reopen it. Never close an issue silently without explaining why.

**Settling finished threads.** Any agent thread (`Triage #N`, `Build #N`, worker, or sweep) that has delivered its work and has nothing left to do — merged, released, answered, or handed off to `Chief of staff` / waiting on external pre-release feedback with no open maintainer decision — must settle itself at the end of its turn (`t3_thread_organize` with `action: "settle"`), so the thread list stays clean. `Chief of staff` also settles completed worker/triage/build threads and removes their clean worktrees.

## GitHub releases

- `.github/workflows/release.yaml` tests the tagged commit, sets the manifest `version` and attaches `better_xiaomi_cloud_map_extractor.zip` when a GitHub release is published. Check it when changing packaging, and check `hacs.json` (`zip_release`, `filename`) with it.
- Releases use the fork's own `vX.Y.Z` tags in sequence from the last release (`gh release list --limit 1`). Check the tag is free (`git ls-remote --tags origin vX.Y.Z`) before `gh release create`.
- The fork carries only its own `vX.Y.Z` tags, starting at `v0.1.0`; the inherited upstream tags were deleted and must never come back. In every checkout run `git config remote.upstream.tagOpt --no-tags` and `git config remote.origin.tagOpt --no-tags`, fetch upstream with `--no-tags`, never `git push --tags` (push one tag by name: `git push origin vX.Y.Z`), and refer to upstream versions by commit SHA (v3.0.0-alpha-24 is `688fe3e`).
- Every release adds its `CHANGELOG.md` section in the same push, crediting imported upstream pull requests and their authors.
- A change we cannot verify ourselves goes out as a pre-release first: keep the pull request open, push a `vX.Y.Z-beta.N` tag on its head, publish it with `--prerelease` so the stable release stays latest, and check the uploaded zip carries the change. Ask the reporter on the fork's issue to have their AI agent install it (HACS → Redownload → pick the beta version), test it and report back, with a short prompt that only links that comment, plus the manual install and revert steps and exactly what to report. Merge and release the stable version after they confirm.
- Run `gh` in the authorized local environment. `gh repo set-default` points at the fork (`Igloczek/better-xiaomi-cloud-map-extractor`). A pull request tool once opened pull requests on upstream by mistake: after creating any pull request, check its URL is on the fork, and if a tool targets upstream, use `gh pr create -R Igloczek/better-xiaomi-cloud-map-extractor` instead.

## Privacy

Debug logs, diagnostics and issue attachments of this integration can contain Xiaomi account tokens, `ssecurity`, `serviceToken`, `passToken`, device tokens, IPs and map images. Never paste them into commits, tests or GitHub; scrub them first. Code that logs or exports session data must redact it.
