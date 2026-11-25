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

## Building, shipping, and Chief of staff

This repository is run ~99% autonomously (see `AGENTS.md`). Triage decides sensible option names,
shapes and opt-in enhancements itself instead of bouncing routine design questions to the
maintainer.

- **Build what is safe and sensible:** when a bug reproduces or a feature/follow-up request is real,
  non-breaking, does not drastically change UX and cannot degrade overall quality, start a
  `Build #<number>` thread and notify the repository's standing `Chief of staff` thread
  (`t3_thread_search` for `Chief of staff`).
- **Locally proven changes ship autonomously:** a build thread follows `AGENTS.md`, proves the
  change with red-first tests and local checks (`ruff check .`, `pytest`), opens a pull request, and
  hands it to `Chief of staff` for review, merge and batched release — without waiting for
  maintainer approval.
- **Hardware, regions or accounts we do not own:** if the change genuinely cannot be verified
  without the reporter's vacuum, region or account, the build thread ships a `vX.Y.Z-beta.N`
  pre-release on the pull request head ("GitHub releases" in `AGENTS.md`) and comments on the issue
  asking the reporter's AI agent to install, test and report back.
- **Follow-ups and beta replies:** when a user replies to confirm a pre-release, report a problem
  with a beta, provide requested data, or ask for a concrete non-breaking follow-up on a closed
  issue, route the summary (in your own words) to the existing `Build #<number>` thread or to
  `Chief of staff` so it is acted on without involving the maintainer.
- **Always mark agent comments:** every GitHub comment posted autonomously by any agent (triage,
  build, or `Chief of staff`) must start with `<!-- triage-agent -->` followed by
  `> 🤖 **AI agent reply.** ...` so it is never mistaken for the maintainer.
- **Always settle finished threads:** escalate to the maintainer only when a change is breaking,
  drastically changes UX for everyone, or risks degrading overall quality/experience (~1%).
  Otherwise, both triage and build threads settle themselves (`t3_thread_organize` with
  `action: "settle"`) as soon as their work is delivered and handed off with nothing left to do.

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
- **Testing & feedback issues:** issues opened to test unmerged pre-releases (currently #24, #29)
  stay open only until their fix ships in a stable release; once shipped, close them as `completed`
  with a clear, marked closing comment stating what version fixed it, what was verified, and that
  replying with a problem will reopen it (never close an issue silently). When confirmation or
  captures arrive — even for a single model on a multi-model testing issue — route them immediately
  to `Chief of staff` so confirmed fixes are merged, shipped and closed right away. If a user later
  comments on a closed issue reporting a bug or regression, reopen it and act on it. Never post an
  empty "recorded, waiting for others" reply that triggers no action; every comment should explain
  a concrete action or next step (closing as fixed in `vX.Y.Z`, routing for merge/release,
  launching a build, or asking one specific follow-up question).
