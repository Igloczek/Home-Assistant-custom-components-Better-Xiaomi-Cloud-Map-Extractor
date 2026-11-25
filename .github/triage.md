# Issue triage

This repository is triaged by the issue triage agent (`Igloczek/issue-triage-agent`, private).
Its procedure applies; this file adds what is specific to Better Xiaomi Cloud Map Extractor.

- **Maintainer:** `Igloczek`
- **Docs:** `README.md`, `docs/report-a-problem.md`, `FORK.md`, `TRIAGE.md`; released changes in
  `CHANGELOG.md`.
- **Roadmap:** `ROADMAP.md`
- **Releases:** `gh release list` (a version a user names may not exist).

## Reproducing

A run may use the local checks (`.venv/bin/ruff check .`, `.venv/bin/pytest`) and inspect the
integration source and mocked fixtures under `tests/`. It never touches the maintainer's own Home
Assistant (`ha.igielski.dev`). A fix thread follows `AGENTS.md`.

## Shipping a test version

A build thread follows `AGENTS.md`: it sorts the change into a class, and a class 1 bug fix merges
as usual. Anything else built for an issue (or anything depending on a vacuum, region or account
state we do not own) stays an open pull request and ships as a `vX.Y.Z-beta.N` pre-release on its
head ("GitHub releases" in `AGENTS.md`), with a comment on the issue asking the reporter's AI agent
to install, test and report back. The build thread ends with the class 2 hand-off for the maintainer
in its own chat; merging and the stable release wait for their approval.

## Project-specific rules

- **Bugs** need the vacuum model, integration and Home Assistant versions, map format (`Used map API`),
  and diagnostics or debug logs. When reproducing needs the user's setup data, give them the
  `report-problem` prompt from `docs/report-a-problem.md`: their AI agent collects and redacts it.
- **Agent reports** carry `<!-- agent-report:v1 -->` in the body or a comment. Check one with
  `python3 scripts/agent-report/cli.py check <file>`; label a complete one `agent-report`, and for
  an incomplete one ask for the missing fields by name.
- **Leaked Xiaomi secrets:** in addition to Home Assistant tokens, watch for real `serviceToken`,
  `ssecurity`, `psecurity`, `passToken`, `clientSign`, 32-hex vacuum tokens, `enc_key`, passwords,
  and signed map download URLs (`last_used_url`). Edit them out, tell the user to change their
  Xiaomi password (or reset the token) to invalidate the leaked session, and note that edit history
  keeps them until deleted.
- **Roborock-app-only vacuums:** vacuums paired only with the Roborock app are not in the Xiaomi
  cloud and cannot be read by this integration. Point the user to Home Assistant's built-in
  Roborock integration and close.
- **Map card problems:** room taps, dashboard overlays, zoom, rotation, tiles, and YAML card config
  belong to `Igloczek/lovelace-better-xiaomi-vacuum-map-card`. Redirect the user there.
- **Testing & feedback issues:** issues opened by the maintainer to collect confirmation or captures
  from owners (#9, #10, #14, #20, #24, #27, #29) wait for outside comments. Acknowledge feedback or
  captures that arrive, and name them in the report for the maintainer.
