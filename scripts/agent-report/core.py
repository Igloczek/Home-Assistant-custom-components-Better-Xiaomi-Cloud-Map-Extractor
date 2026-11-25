"""Agent problem reports: collection shaping, redaction, rendering and validation.

Pure functions only (Python 3 standard library, no dependencies). The CLI (cli.py)
does the I/O, so the redaction rules that make an unattended report safe to publish
are tested directly in tests/test_agent_report.py.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from typing import Any

DOMAIN = "better_xiaomi_cloud_map_extractor"
ORIGINAL_DOMAIN = "xiaomi_cloud_map_extractor"
DOMAINS = (DOMAIN, ORIGINAL_DOMAIN)

SCHEMA_VERSION = 1
REPO = "Igloczek/better-xiaomi-cloud-map-extractor"
MARKER = "<!-- agent-report:v1 -->"
DATA_START = "<!-- agent-report-data:start -->"
DATA_END = "<!-- agent-report-data:end -->"
BODY_LIMIT = 60_000
LOG_LIMIT = 25

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "agent.name",
    "integration.version",
    "home_assistant.version",
    "vacuum.model",
    "problem.summary",
    "problem.expected",
    "problem.actual",
    "problem.reproduction",
)

# Keys whose values are always dropped on sight in collected data.
DROP_KEYS_RE = re.compile(
    r"^(?:"
    r"access_token|token|refresh_token|password|passwd|secret|api_key|apikey|"
    r"auth|authorization|cookie|cookies|serial|serial_number|sn|wifi_sn|"
    r"mac|mac_address|bssid|ssid|wifi_ssid|ip|ip_address|local_ip|host|"
    r"email|username|user_id|userid|c_user_id|cuserid|did|device_id|"
    r"latitude|longitude|location|address|identifiers|connections|"
    r"external_url|internal_url|webhook_id|cloudhook_url|"
    r"ssecurity|psecurity|service_token|servicetoken|pass_token|passtoken|"
    r"client_sign|clientsign|enc_key|last_used_url|signature|sign|"
    r"session|stored_session|raw_map|map_bytes|image_bytes"
    r")$",
    re.IGNORECASE,
)

# High-confidence secret patterns. Run on everything, including issue bodies in `check`.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-token>",
    ),
    (
        "bearer",
        re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
        r"\1<redacted-token>",
    ),
    (
        "url-token",
        re.compile(
            r"([?&](?:token|access_token|auth|api_key|apikey|key|authSig|"
            r"ssecurity|psecurity|serviceToken|passToken|clientSign|_sign|"
            r"Signature|Expires|OSSAccessKeyId|X-Amz-Signature|GalaxyAccessKeyId)=)"
            r"[^&\s\"'<>)]+",
            re.IGNORECASE,
        ),
        r"\1<redacted>",
    ),
    (
        "url-credentials",
        re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/:@\"']+:[^\s/@\"']+@"),
        r"\1<redacted>@",
    ),
    (
        "xiaomi-secret-kv",
        re.compile(
            r"(\b(?:serviceToken|ssecurity|psecurity|passToken|clientSign|"
            r"enc_key|Enc Key|wifi_sn)\b\s*[:=]\s*['\"]?)([^\s,'\"}\]]{4,})",
            re.IGNORECASE,
        ),
        r"\1<redacted>",
    ),
    (
        "device-token-kv",
        re.compile(
            r"(\b(?:device_token|vacuum_token|miio_token|token|password)\b\s*[:=]\s*['\"]?)"
            r"([0-9a-fA-F]{32}\b|[^\s,'\"}\]]{6,})",
            re.IGNORECASE,
        ),
        r"\1<redacted>",
    ),
)

# Personal-data patterns run on collected data.
PERSONAL_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    ("mac", re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b"), "<mac>"),
    (
        "ipv4",
        re.compile(
            r"\b(?!127\.0\.0\.1\b)(?:25[0-5]|2[0-4]\d|1?\d?\d)"
            r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
        ),
        "<ip>",
    ),
    ("home-path", re.compile(r"(/(?:Users|home)/)[^/\s\"']+"), r"\1<user>"),
    ("hex32", re.compile(r"\b[0-9a-fA-F]{32}\b"), "<redacted-hex32>"),
)

UNIQUE_ID_CHUNK_RE = re.compile(r"[0-9A-Fa-f]{2}(?:[:-]?[0-9A-Fa-f]{2}){5,}|[0-9A-Za-z]{12,}|\d{6,}")
PURE_HEX_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}(?:[:-]?[0-9A-Fa-f]{2}){5,})$")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def scrub_secrets(text: str | None) -> tuple[str, dict[str, int]]:
    """Replace high-confidence secrets in text and return (scrubbed_text, hit_counts)."""
    hits: dict[str, int] = {}
    out = "" if text is None else str(text)
    for name, pattern, repl in SECRET_PATTERNS:
        count = 0

        def _replace(match: re.Match[str], _repl: str = repl) -> str:
            nonlocal count
            # Ignore placeholders that were already redacted.
            matched = match.group(0)
            if "<redacted" in matched:
                return matched
            count += 1
            return match.expand(_repl)

        out = pattern.sub(_replace, out)
        if count:
            hits[name] = hits.get(name, 0) + count
    return out, hits


def _scrub_string(value: str, tally: dict[str, int]) -> str:
    out, secret_hits = scrub_secrets(value)
    for key, count in secret_hits.items():
        tally[key] = tally.get(key, 0) + count
    for name, pattern, repl in PERSONAL_PATTERNS:
        count = 0

        def _replace(match: re.Match[str], _repl: str = repl) -> str:
            nonlocal count
            count += 1
            return match.expand(_repl)

        out = pattern.sub(_replace, out)
        if count:
            tally[name] = tally.get(name, 0) + count
    return out


def _scrub_unique_id(unique_id: str | None, aliases: dict[str, str]) -> str:
    text = "" if unique_id is None else str(unique_id)

    def _replace(match: re.Match[str]) -> str:
        chunk = match.group(0)
        if not any(ch.isdigit() for ch in chunk) and not PURE_HEX_MAC_RE.match(chunk):
            return chunk
        if chunk not in aliases:
            aliases[chunk] = f"ID{len(aliases) + 1}"
        return aliases[chunk]

    return UNIQUE_ID_CHUNK_RE.sub(_replace, text)


def _pick_registry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entry.get("entity_id"),
        "unique_id": entry.get("unique_id"),
        "platform": entry.get("platform"),
        "config_entry_id": entry.get("config_entry_id"),
        "device_id": entry.get("device_id"),
        "translation_key": entry.get("translation_key"),
        "original_name": entry.get("original_name"),
        "has_entity_name": entry.get("has_entity_name"),
        "entity_category": entry.get("entity_category"),
        "disabled_by": entry.get("disabled_by"),
        "hidden_by": entry.get("hidden_by"),
        "renamed": bool(entry.get("name")),
    }


def _pick_device(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": device.get("id"),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model"),
        "model_id": device.get("model_id"),
        "sw_version": device.get("sw_version"),
        "hw_version": device.get("hw_version"),
        "disabled_by": device.get("disabled_by"),
        "renamed": bool(device.get("name_by_user")),
    }


def _pick_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry.get("entry_id"),
        "domain": entry.get("domain"),
        "state": entry.get("state"),
        "reason": entry.get("reason"),
        "disabled_by": entry.get("disabled_by"),
        "source": entry.get("source"),
        "version": entry.get("version"),
        "minor_version": entry.get("minor_version"),
        "pref_disable_new_entities": entry.get("pref_disable_new_entities"),
        "pref_disable_polling": entry.get("pref_disable_polling"),
    }


def _pick_repairs(issues: list[Any]) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        if item.get("domain") not in DOMAINS:
            continue
        picked.append(
            {
                "domain": item.get("domain"),
                "issue_id": item.get("issue_id"),
                "translation_key": item.get("translation_key"),
                "translation_placeholders": item.get("translation_placeholders"),
                "severity": item.get("severity"),
                "is_fixable": item.get("is_fixable"),
                "ignored": item.get("ignored"),
            }
        )
    return picked


def _pick_logs(system_log: list[Any]) -> list[dict[str, Any]]:
    keywords = (
        DOMAIN,
        ORIGINAL_DOMAIN,
        "vacuum_map_parser",
        "xiaomi_cloud",
        "miio",
    )
    matched: list[dict[str, Any]] = []
    for entry in system_log:
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message")
        parts = [entry.get("name"), entry.get("source")]
        if isinstance(msg, list):
            parts.extend(msg)
        else:
            parts.append(msg)
        haystack = " ".join(str(p) for p in parts if p is not None).lower()
        if any(word in haystack for word in keywords):
            matched.append(
                {
                    "level": entry.get("level"),
                    "name": entry.get("name"),
                    "message": msg,
                    "source": entry.get("source"),
                    "count": entry.get("count"),
                    "first_occurred": entry.get("first_occurred"),
                }
            )
        if len(matched) >= LOG_LIMIT:
            break
    return matched


def _select_entry(entries: list[dict[str, Any]], selector: str | int | None) -> dict[str, Any] | None:
    if not entries:
        return None
    if selector is not None:
        if isinstance(selector, int) and 0 <= selector < len(entries):
            return entries[selector]
        sel_str = str(selector)
        if sel_str.isdigit() and 0 <= int(sel_str) < len(entries):
            return entries[int(sel_str)]
        for entry in entries:
            if entry.get("entry_id") == sel_str:
                return entry
    # Prefer our fork's enabled entries first.
    for entry in entries:
        if entry.get("domain") == DOMAIN and not entry.get("disabled_by"):
            return entry
    for entry in entries:
        if entry.get("domain") == DOMAIN:
            return entry
    return entries[0]


def _extract_diagnostics(raw: dict[str, Any], entry_id: str | None) -> dict[str, Any]:
    diag_map = _as_dict(raw.get("diagnostics"))
    if entry_id and isinstance(diag_map.get(entry_id), dict):
        return diag_map[entry_id]
    if "config_entry_data" in diag_map or "device_data" in diag_map:
        return diag_map
    if "data" in diag_map and isinstance(diag_map["data"], dict):
        return diag_map["data"]
    return {}


def build_report(raw: dict[str, Any], opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Select integration-relevant data from raw Home Assistant payloads and redact it."""
    opts = opts or {}
    all_entries = [e for e in _as_list(raw.get("config_entries")) if isinstance(e, dict)]
    fork_entries = [e for e in all_entries if e.get("domain") == DOMAIN]
    original_entries = [e for e in all_entries if e.get("domain") == ORIGINAL_DOMAIN]
    selected_entry = _select_entry(fork_entries or original_entries, opts.get("entry"))
    entry_id = selected_entry.get("entry_id") if selected_entry else None

    registry = [e for e in _as_list(raw.get("entity_registry")) if isinstance(e, dict)]
    devices = [d for d in _as_list(raw.get("device_registry")) if isinstance(d, dict)]
    states = [s for s in _as_list(raw.get("states")) if isinstance(s, dict)]
    manifests = _as_dict(raw.get("manifests"))

    if entry_id:
        relevant_reg = [e for e in registry if e.get("config_entry_id") == entry_id]
    else:
        relevant_reg = [e for e in registry if e.get("platform") in DOMAINS]

    relevant_entity_ids = {e["entity_id"] for e in relevant_reg if e.get("entity_id")}
    relevant_device_ids = {e["device_id"] for e in relevant_reg if e.get("device_id")}
    if entry_id:
        for device in devices:
            if entry_id in _as_list(device.get("config_entries")):
                if device.get("id"):
                    relevant_device_ids.add(device["id"])

    relevant_devices = [d for d in devices if d.get("id") in relevant_device_ids]
    relevant_states = {
        s["entity_id"]: {"state": s.get("state"), "attributes": s.get("attributes") or {}}
        for s in states
        if s.get("entity_id") in relevant_entity_ids
    }

    diagnostics = _extract_diagnostics(raw, entry_id)
    entry_diag = _as_dict(diagnostics.get("config_entry_data"))
    device_diag = _as_dict(diagnostics.get("device_data"))
    entry_data = _as_dict(entry_diag.get("data"))
    entry_options = _as_dict(entry_diag.get("options"))

    primary_device = relevant_devices[0] if relevant_devices else {}
    vacuum_model = (
        opts.get("model")
        or entry_data.get("model")
        or device_diag.get("model")
        or primary_device.get("model")
    )
    map_format = (
        opts.get("map_format")
        or entry_data.get("used_map_api")
        or entry_data.get("force_api")
        or device_diag.get("used_map_api")
    )
    server = entry_data.get("server") or device_diag.get("server")

    fork_manifest = _as_dict(manifests.get(DOMAIN))
    orig_manifest = _as_dict(manifests.get(ORIGINAL_DOMAIN))
    integration_version = opts.get("integration_version") or fork_manifest.get("version")

    ha_config = _as_dict(raw.get("ha_config"))

    collection_errors = [
        f"{k}: {v['__error']}"
        for k, v in raw.items()
        if isinstance(v, dict) and "__error" in v
    ]

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now_iso(),
        "agent": {
            "name": opts.get("agent_name") or "",
            "model": opts.get("agent_model") or "",
        },
        "integration": {
            "domain": selected_entry.get("domain") if selected_entry else DOMAIN,
            "version": integration_version,
            "entries_found": len(fork_entries),
            "original_installed": bool(orig_manifest) or bool(original_entries),
            "original_version": orig_manifest.get("version"),
            "original_entries_found": len(original_entries),
            "entry": _pick_entry(selected_entry) if selected_entry else None,
        },
        "home_assistant": {
            "version": ha_config.get("version"),
            "installation_type": opts.get("installation_type"),
            "safe_mode": ha_config.get("safe_mode"),
        },
        "vacuum": {
            "model": vacuum_model,
            "map_format": map_format,
            "server": server,
            "manufacturer": primary_device.get("manufacturer"),
            "sw_version": primary_device.get("sw_version"),
            "lan_reachable": device_diag.get("lan_reachable"),
            "map_status": device_diag.get("map_status"),
        },
        "config_options": entry_options,
        "diagnostics": {
            "config_entry_data": {
                k: v
                for k, v in entry_diag.items()
                if k not in ("title", "unique_id")
            }
            if entry_diag
            else {},
            "device_data": device_diag,
        },
        "repairs": _pick_repairs(_as_list(raw.get("repairs"))),
        "states": relevant_states,
        "registry": [_pick_registry(e) for e in relevant_reg],
        "devices": [_pick_device(d) for d in relevant_devices],
        "logs": {
            "ha": _pick_logs(_as_list(raw.get("system_log"))),
        },
        "problem": {
            "summary": "",
            "expected": "",
            "actual": "",
            "reproduction": [],
            "diagnosis": "",
            "workaround": "",
            "related": [],
        },
        "testability": "needs-fixture",
        "redactions": [],
        "collection_errors": collection_errors,
    }
    return redact_report(report)


def redact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Deterministically redact secrets, personal data, and identifiers from a report."""
    tally: dict[str, int] = {}
    entity_aliases: dict[str, str] = {}
    device_aliases: dict[str, str] = {}
    entry_aliases: dict[str, str] = {}
    id_aliases: dict[str, str] = {}
    used_entity_aliases: set[str] = set()

    def _alias_entity(entity_id: str | None, hint: str | None = None) -> None:
        if not entity_id or entity_id in entity_aliases or "." not in entity_id:
            return
        domain = entity_id.split(".", 1)[0]
        slug = re.sub(r"[^a-z0-9_]", "_", (hint or "entity").lower()).strip("_") or "entity"
        base = f"{domain}.{slug}"
        alias = base
        counter = 2
        while alias in used_entity_aliases:
            alias = f"{base}_{counter}"
            counter += 1
        used_entity_aliases.add(alias)
        entity_aliases[entity_id] = alias

    for reg in _as_list(report.get("registry")):
        if isinstance(reg, dict):
            _alias_entity(reg.get("entity_id"), reg.get("translation_key"))
            eid = reg.get("config_entry_id")
            if isinstance(eid, str) and eid and eid not in entry_aliases:
                entry_aliases[eid] = f"entry_{len(entry_aliases) + 1}"
    for entity_id in _as_dict(report.get("states")):
        _alias_entity(entity_id, None)

    for device in _as_list(report.get("devices")):
        if isinstance(device, dict):
            did = device.get("id")
            if isinstance(did, str) and did and did not in device_aliases:
                device_aliases[did] = f"device_{len(device_aliases) + 1}"

    entry_obj = _as_dict(_as_dict(report.get("integration")).get("entry"))
    if isinstance(entry_obj.get("entry_id"), str) and entry_obj["entry_id"]:
        eid = entry_obj["entry_id"]
        if eid not in entry_aliases:
            entry_aliases[eid] = f"entry_{len(entry_aliases) + 1}"
    diag_entry = _as_dict(_as_dict(report.get("diagnostics")).get("config_entry_data"))
    if isinstance(diag_entry.get("entry_id"), str) and diag_entry["entry_id"]:
        eid = diag_entry["entry_id"]
        if eid not in entry_aliases:
            entry_aliases[eid] = f"entry_{len(entry_aliases) + 1}"

    sorted_entities = sorted(entity_aliases.keys(), key=len, reverse=True)
    entity_re = (
        re.compile(
            r"(?<![a-z0-9_.])("
            + "|".join(re.escape(eid) for eid in sorted_entities)
            + r")(?![a-z0-9_])"
        )
        if sorted_entities
        else None
    )

    sorted_entries = sorted(entry_aliases.keys(), key=len, reverse=True)
    entry_re = (
        re.compile("|".join(re.escape(eid) for eid in sorted_entries))
        if sorted_entries
        else None
    )

    def _alias_text(text: str) -> str:
        out = text
        if entity_re:
            out = entity_re.sub(lambda m: entity_aliases[m.group(0)], out)
        if entry_re:
            out = entry_re.sub(lambda m: entry_aliases[m.group(0)], out)
        return out

    def _walk(value: Any, key: str | None = None) -> Any:
        if key == "device_id" and isinstance(value, str) and value in device_aliases:
            return device_aliases[value]
        if key == "id" and isinstance(value, str) and value in device_aliases:
            return device_aliases[value]
        if key in ("entry_id", "config_entry_id") and isinstance(value, str) and value in entry_aliases:
            return entry_aliases[value]
        if key and DROP_KEYS_RE.match(key) and value not in (None, "", [], {}):
            tally[f"field:{key}"] = tally.get(f"field:{key}", 0) + 1
            return "<redacted>"
        if key == "entity_picture" and isinstance(value, str):
            tally["entity_picture"] = tally.get("entity_picture", 0) + 1
            base = _alias_text(value.split("?", 1)[0])
            return f"{base}?<redacted>"
        if key == "friendly_name" and isinstance(value, str):
            tally["friendly_name"] = tally.get("friendly_name", 0) + 1
            return "<friendly name>"
        if key == "unique_id" and isinstance(value, str):
            return _scrub_unique_id(value, id_aliases)
        if isinstance(value, str):
            return _scrub_string(_alias_text(value), tally)
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for k, v in value.items():
                new_key = _alias_text(k) if "." in k else k
                result[new_key] = _walk(v, k)
            return result
        return value

    out = _walk(copy.deepcopy(report))
    redactions = [f"{what} ×{count}" for what, count in tally.items()]
    if entity_aliases:
        redactions.append(f"entity ids aliased ×{len(entity_aliases)}")
    if id_aliases:
        redactions.append(f"device-specific unique_id chunks aliased ×{len(id_aliases)}")
    if device_aliases:
        redactions.append(f"device ids aliased ×{len(device_aliases)}")
    if entry_aliases:
        redactions.append(f"config entry ids aliased ×{len(entry_aliases)}")
    redactions.extend(["cloud session store not collected", "map image not collected"])
    out["redactions"] = redactions
    return out


# ---------------------------------------------------------------------------------------------
# Validation, fitting and rendering
# ---------------------------------------------------------------------------------------------


def _get_path(obj: dict[str, Any], path: str) -> Any:
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _is_blank(value: Any) -> bool:
    if value in (None, "", "unknown"):
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def missing_fields(report: dict[str, Any]) -> list[str]:
    return [path for path in REQUIRED_FIELDS if _is_blank(_get_path(report, path))]


def issue_title(report: dict[str, Any]) -> str:
    summary = str(_as_dict(report.get("problem")).get("summary") or "")
    first = re.split(r"[.\n]", summary, maxsplit=1)[0].strip()
    vacuum = _as_dict(report.get("vacuum"))
    integration = _as_dict(report.get("integration"))
    where = ", ".join(str(x) for x in (vacuum.get("model"), integration.get("version")) if x)
    base = first or "Integration problem"
    title = f"{base} ({where})" if where else base
    return title[:120]


def _trim_arrays(value: Any, maximum: int) -> Any:
    if isinstance(value, list):
        items = [_trim_arrays(item, maximum) for item in value[:maximum]]
        if len(value) > maximum:
            items.append(f"… {len(value) - maximum} more trimmed")
        return items
    if isinstance(value, dict):
        return {k: _trim_arrays(v, maximum) for k, v in value.items()}
    return value


def fit_report(report: dict[str, Any], limit: int = BODY_LIMIT) -> dict[str, Any]:
    """Trim large arrays/blocks until the rendered Markdown fits under GitHub's body limit."""
    if len(_render_issue_body_unfitted(report)) <= limit:
        return report
    current = copy.deepcopy(report)
    for maximum in (50, 10, 3):
        candidate = copy.deepcopy(current)
        candidate["diagnostics"] = _trim_arrays(report.get("diagnostics"), maximum)
        candidate["states"] = _trim_arrays(report.get("states"), maximum)
        if len(_render_issue_body_unfitted(candidate)) <= limit:
            candidate["redactions"] = [
                *_as_list(candidate.get("redactions")),
                f"long arrays trimmed to {maximum} items",
            ]
            return candidate
        current = candidate

    current["states"] = {
        eid: {"state": s.get("state")}
        for eid, s in _as_dict(current.get("states")).items()
        if isinstance(s, dict)
    }
    current["redactions"] = [*_as_list(current.get("redactions")), "state attributes dropped for size"]
    if len(_render_issue_body_unfitted(current)) <= limit:
        return current

    current["diagnostics"] = {"__trimmed": "too large for an issue body"}
    current["redactions"] = [*_as_list(current.get("redactions")), "diagnostics dropped for size"]
    return current


def render_issue_body(report: dict[str, Any]) -> str:
    return _render_issue_body_unfitted(fit_report(report))


def _render_issue_body_unfitted(report: dict[str, Any]) -> str:
    problem = _as_dict(report.get("problem"))
    agent = _as_dict(report.get("agent"))
    integration = _as_dict(report.get("integration"))
    ha = _as_dict(report.get("home_assistant"))
    vacuum = _as_dict(report.get("vacuum"))
    entry = _as_dict(integration.get("entry"))

    agent_label = agent.get("name") or "an agent"
    if agent.get("model"):
        agent_label = f"{agent_label} ({agent['model']})"

    reproduction = _as_list(problem.get("reproduction"))
    repro_lines = (
        [f"{idx}. {step}" for idx, step in enumerate(reproduction, start=1)]
        if reproduction
        else ["_missing_"]
    )

    ha_cell = str(ha.get("version") or "?")
    if ha.get("installation_type"):
        ha_cell = f"{ha_cell}, {ha['installation_type']}"

    orig_note = ""
    if integration.get("original_installed"):
        orig_ver = integration.get("original_version") or "installed"
        orig_note = f"; original `xiaomi_cloud_map_extractor` ({orig_ver}, {integration.get('original_entries_found', 0)} entries)"

    vacuum_parts = [p for p in (vacuum.get("manufacturer"), vacuum.get("model")) if p]
    vacuum_cell = " ".join(str(p) for p in vacuum_parts) or "?"
    if vacuum.get("map_format"):
        vacuum_cell += f" (format: `{vacuum['map_format']}`)"
    if vacuum.get("server"):
        vacuum_cell += f", region `{vacuum['server']}`"

    status_bits = []
    if entry.get("state"):
        status_bits.append(f"entry `{entry['state']}`")
    if vacuum.get("map_status"):
        status_bits.append(f"map `{vacuum['map_status']}`")
    if vacuum.get("lan_reachable") is not None:
        status_bits.append(f"LAN reachable: `{vacuum['lan_reachable']}`")
    status_cell = ", ".join(status_bits) or "unknown"

    lines = [
        MARKER,
        "> [!NOTE]",
        (
            f"> 🤖 **Agent report.** Collected and filed by {agent_label} with the "
            f"[report-problem skill](https://github.com/{REPO}/blob/master/.agents/skills/report-problem/SKILL.md). "
            "Data was redacted automatically; credentials, the Xiaomi cloud session, and the map image were not collected."
        ),
        "",
        "## Summary",
        str(problem.get("summary") or "_missing_"),
        "",
        "## Expected",
        str(problem.get("expected") or "_missing_"),
        "",
        "## Actual",
        str(problem.get("actual") or "_missing_"),
        "",
        "## Reproduction",
        *repro_lines,
        "",
        "## Environment",
        "| | |",
        "|---|---|",
        f"| Integration | `{integration.get('domain') or DOMAIN}` {integration.get('version') or '?'}{orig_note} |",
        f"| Home Assistant | {ha_cell} |",
        f"| Vacuum | {vacuum_cell} |",
        f"| Status | {status_cell} |",
        f"| Testability | `{report.get('testability') or 'needs-fixture'}` |",
        "",
    ]

    if problem.get("diagnosis"):
        lines.extend(["## Diagnosis", str(problem["diagnosis"]), ""])
    if problem.get("workaround"):
        lines.extend(["## Workaround", str(problem["workaround"]), ""])
    related = _as_list(problem.get("related"))
    if related:
        lines.extend(["## Related", *(f"- {item}" for item in related), ""])

    lines.extend(
        [
            "## Redactions",
            " · ".join(str(r) for r in _as_list(report.get("redactions"))) or "none",
            "",
            DATA_START,
            "<details><summary>Report data (JSON, regression-fixture ready)</summary>",
            "",
            "```json",
            json.dumps(report, indent=2, ensure_ascii=False),
            "```",
            "",
            "</details>",
            DATA_END,
            "",
        ]
    )
    return "\n".join(lines)


def parse_issue_body(body: str | None) -> dict[str, Any]:
    text = "" if body is None else str(body)
    is_agent = MARKER in text
    if not is_agent:
        return {"isAgent": False, "report": None, "error": None}
    start = text.find(DATA_START)
    end = text.find(DATA_END)
    if start < 0 or end < start:
        return {"isAgent": True, "report": None, "error": "report data block not found"}
    match = re.search(r"```json\s*([\s\S]*?)```", text[start:end])
    if not match:
        return {"isAgent": True, "report": None, "error": "report data JSON not found"}
    try:
        return {"isAgent": True, "report": json.loads(match.group(1)), "error": None}
    except json.JSONDecodeError as err:
        return {"isAgent": True, "report": None, "error": f"report data JSON does not parse: {err}"}


def review_issue(body: str | None) -> dict[str, Any]:
    """Validate an issue body: agent marker, required fields, and leaked secrets."""
    scrubbed, secret_hits = scrub_secrets(body)
    parsed = parse_issue_body(scrubbed)
    missing = missing_fields(parsed["report"]) if isinstance(parsed["report"], dict) else []
    complete = bool(parsed["isAgent"]) and not parsed["error"] and len(missing) == 0
    return {
        "isAgent": parsed["isAgent"],
        "complete": complete,
        "error": parsed["error"],
        "missing": missing,
        "redactedBody": scrubbed if secret_hits else None,
        "secretHits": secret_hits,
    }
