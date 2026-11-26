# Report a problem

Something wrong with your vacuum map, Xiaomi login, repairs or migration? Let an AI agent report it
for you. You describe the problem in your own words; the agent collects your integration
diagnostics, repairs and logs, removes all private data, works out what it can, shows you the
redacted report to confirm, and files a report that can be reproduced immediately.

You do less work, and the report arrives with the versions, vacuum model, map format and scrubbed
diagnostics needed to fix it. Agent reports are labelled **`agent-report`** on the tracker and get
handled first.

## What you need

- An AI agent that can run commands on your computer, such as **Claude Code**, **Codex**,
  **Cursor**, or a similar coding agent. A chat window that cannot run commands is not enough.
- Python 3 installed (macOS and Linux include it) and your Home Assistant reachable.

You do not need to edit `configuration.yaml`, dig through log files or redact tokens by hand.

## Three steps

### 1. Give your agent this instruction

Copy it, and replace the last line with your problem:

```text
Read https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/.agents/skills/report-problem/SKILL.md
and follow it to report this problem with my Xiaomi vacuum map:

<describe what is wrong, e.g. "my Xiaomi S12 map fails to update while cleaning">
```

Describe what you see, not what you think the cause is. "Login succeeds, but the map entity stays
unavailable after starting a clean" is perfect.

### 2. Give it access when it asks

The agent will ask for a temporary Home Assistant access key (a "long-lived access token"):

1. In Home Assistant, click your name in the bottom-left corner.
2. Open the **Security** tab.
3. Scroll to **Long-lived access tokens** and choose **Create token**.
4. Name it `vacuum map report`, copy the token, and paste it to the agent with the address you use
   to open Home Assistant (for example `http://homeassistant.local:8123`).

The agent only reads. Delete the token on the same page when the report is filed.

If your agent can control your browser, it may use your open Home Assistant tab instead and not
ask for a token.

### 3. Review and confirm

The agent shows you the rendered, redacted report (title, summary, options, room names, diagnostics
and log lines) and waits for your **yes** before posting it publicly on GitHub. Once you confirm,
it files the report and gives you the link. If it finds that the problem is a setting (such as map
saving turned off in Xiaomi Home, a Roborock-app-only vacuum, or an outdated version) rather than a
bug, it tells you how to fix it instead of filing anything.

## If the maintainer asks something

You will get a GitHub notification. Give your agent the issue link:

```text
Read https://raw.githubusercontent.com/Igloczek/better-xiaomi-cloud-map-extractor/master/.agents/skills/report-problem/SKILL.md
and answer the questions on <issue link>.
```

The same works for an issue you already filed by hand: point your agent at it and it adds the
missing details.

## What leaves your computer

The report contains your integration and Home Assistant versions, vacuum model and map format,
non-sensitive options, redacted diagnostics, active Repairs issues for this integration, entity
states, and related log lines.

It never contains:

- your Home Assistant access token, Xiaomi username, password, or session tokens (`serviceToken`,
  `ssecurity`, `passToken`, `clientSign`);
- your vacuum's local token, map decryption key (`enc_key`), signed download URL (`last_used_url`),
  or WiFi serial number;
- IP addresses, MAC addresses, email addresses, or personal device/entity names (entities are
  renamed to neutral aliases like `image.live_map` and `sensor.map_status`);
- your Xiaomi cloud session store or your map image.

The agent does not change anything in Home Assistant, and nothing is published until you review the
rendered report and confirm. The removal of private data is done by the report tool
(`scripts/agent-report/`), not left to the agent's judgement.

## No agent?

Use the [bug form](https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues/new?template=bug_report.yml).
It asks for your vacuum model, integration and Home Assistant versions, diagnostics and debug logs.
Hand-written reports take longer to reproduce, so triage may ask for missing details or suggest an
agent report.

## For agents and maintainers

The procedure is the [report-problem skill](../.agents/skills/report-problem/SKILL.md); the tooling
is in [`scripts/agent-report/`](../scripts/agent-report/). New issues are triaged by an AI agent
following [`.github/triage.md`](../.github/triage.md).
