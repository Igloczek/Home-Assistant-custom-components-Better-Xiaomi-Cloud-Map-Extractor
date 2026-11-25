# Contributing

Thank you for your contribution! To submit changes, fork this repository and open a pull request
against `master`.

## Development setup

Create a virtual environment with Python 3.14 (matching `.python-version`) and install the test
requirements:

```sh
uv venv -p 3.14 .venv
uv pip install -p .venv -r requirements_test.txt
```

Keep `requirements_test.txt` in step with `custom_components/better_xiaomi_cloud_map_extractor/manifest.json`.

## Local checks

Run these before opening a pull request:

```sh
.venv/bin/ruff check .
.venv/bin/pytest
```

Every bug fix starts with a failing test (red first) that reproduces the bug and closes the gap in
the test suite. See [`AGENTS.md`](AGENTS.md) for the full project map, compatibility rules, and
review/release workflow.

## Privacy in fixtures and tests

Never commit real Xiaomi account credentials, `serviceToken`, `ssecurity`, `passToken`, device
tokens, IP addresses, MAC addresses, or personal map images. Scrub captured payloads first and store
reusable fixtures under `tests/fixtures/`.

## Adding or fixing a vacuum format

1. Read [`FORK.md`](FORK.md) and [`TRIAGE.md`](TRIAGE.md) to check existing differences and open
   model requests.
2. Add a scrubbed capture or synthetic fixture under `tests/fixtures/` and a failing `pytest` test
   in `tests/test_vacuum_<vendor>.py`.
3. Keep all LAN / `python-miio` calls and map parsing off Home Assistant's event loop (the test
   suite enforces blocking-call detection).
4. Record any intentional fork-only behaviour in [`FORK.md`](FORK.md) and
   [`.agents/skills/sync-upstream/fork-differences.md`](.agents/skills/sync-upstream/fork-differences.md).

## Reporting bugs with an AI agent

For bug reports, prefer the [`report-problem` skill](.agents/skills/report-problem/SKILL.md) and
[`docs/report-a-problem.md`](docs/report-a-problem.md) so diagnostics and logs arrive redacted and
ready to turn into a regression test.
