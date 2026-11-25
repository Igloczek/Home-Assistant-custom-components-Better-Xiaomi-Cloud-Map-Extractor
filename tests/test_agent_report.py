"""Tests for scripts/agent-report (collection, redaction, rendering, validation)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_PATH = REPO_ROOT / "scripts" / "agent-report" / "core.py"
CLI_PATH = REPO_ROOT / "scripts" / "agent-report" / "cli.py"

spec = importlib.util.spec_from_file_location("agent_report_core", CORE_PATH)
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def _sample_raw() -> dict[str, Any]:
    return {
        "raw_schema": 1,
        "ha_config": {"version": "2026.11.0", "safe_mode": False},
        "manifests": {
            "better_xiaomi_cloud_map_extractor": {"version": "1.0.0", "is_built_in": False},
            "xiaomi_cloud_map_extractor": {"version": "3.0.0-alpha-24", "is_built_in": False},
        },
        "config_entries": [
            {
                "entry_id": "01JABCDEF1234567890ABCDEF1",
                "domain": "better_xiaomi_cloud_map_extractor",
                "state": "loaded",
                "reason": None,
                "disabled_by": None,
                "source": "user",
                "version": 1,
                "minor_version": 1,
            },
            {
                "entry_id": "01JORIG9999999999999999999",
                "domain": "xiaomi_cloud_map_extractor",
                "state": "not_loaded",
                "disabled_by": "user",
                "source": "user",
            },
        ],
        "entity_registry": [
            {
                "entity_id": "image.kowalski_living_room_live_map",
                "unique_id": "aabbccddeeff_image_live_map",
                "platform": "better_xiaomi_cloud_map_extractor",
                "config_entry_id": "01JABCDEF1234567890ABCDEF1",
                "device_id": "dev-secret-uuid-12345",
                "translation_key": "live_map",
                "original_name": "Live map",
                "has_entity_name": True,
                "disabled_by": None,
                "name": "Kowalski Map",
            },
            {
                "entity_id": "sensor.kowalski_living_room_map_status",
                "unique_id": "aabbccddeeff_sensor_map_status",
                "platform": "better_xiaomi_cloud_map_extractor",
                "config_entry_id": "01JABCDEF1234567890ABCDEF1",
                "device_id": "dev-secret-uuid-12345",
                "translation_key": "map_status",
                "original_name": "Map status",
                "has_entity_name": True,
                "disabled_by": None,
            },
        ],
        "device_registry": [
            {
                "id": "dev-secret-uuid-12345",
                "config_entries": ["01JABCDEF1234567890ABCDEF1"],
                "manufacturer": "Xiaomi",
                "model": "xiaomi.vacuum.b106eu",
                "sw_version": "4.3.3_0050",
                "name_by_user": "Odkurzacz Jana",
            }
        ],
        "states": [
            {
                "entity_id": "image.kowalski_living_room_live_map",
                "state": "2026-11-25T12:00:00+00:00",
                "attributes": {
                    "friendly_name": "Odkurzacz Jana Live map",
                    "entity_picture": "/api/image_proxy/image.kowalski_living_room_live_map?token=abcdef1234567890",
                    "calibration_points": [{"vacuum": {"x": 0, "y": 0}, "map": {"x": 10, "y": 10}}],
                    "rooms": {"1": {"name": "Kitchen"}},
                },
            },
            {
                "entity_id": "sensor.unrelated_temp",
                "state": "21.5",
                "attributes": {"friendly_name": "Bedroom"},
            },
        ],
        "repairs": [
            {
                "domain": "better_xiaomi_cloud_map_extractor",
                "issue_id": "map_saving_disabled_01JABCDEF1234567890ABCDEF1",
                "translation_key": "map_saving_disabled",
                "severity": "warning",
                "is_fixable": False,
                "ignored": False,
            },
            {
                "domain": "other_integration",
                "issue_id": "ignore_me",
            },
        ],
        "diagnostics": {
            "01JABCDEF1234567890ABCDEF1": {
                "config_entry_data": {
                    "entry_id": "01JABCDEF1234567890ABCDEF1",
                    "title": "Odkurzacz Jana",
                    "unique_id": "AA:BB:CC:DD:EE:FF",
                    "data": {
                        "host": "192.168.1.77",
                        "token": "0123456789abcdef0123456789abcdef",
                        "username": "jan.kowalski@example.com",
                        "password": "SuperSecretPassword!",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "model": "xiaomi.vacuum.b106eu",
                        "server": "de",
                        "used_map_api": "IJAI",
                        "ssecurity": "ssec-secret-value-xyz",
                        "serviceToken": "stoken-secret-value-xyz",
                        "passToken": "ptoken-secret-value-xyz",
                    },
                    "options": {
                        "image_config": {"fit_to_explored": True, "scale": 1},
                    },
                },
                "device_data": {
                    "map_status": "MAP_SAVING_DISABLED",
                    "lan_reachable": False,
                    "enc_key": "deadbeef12345678",
                    "last_used_url": "https://fds.xiaomi.com/map/123?GalaxyAccessKeyId=AK123&Signature=SIG456",
                    "wifi_sn": "12345/6789012345678",
                },
            }
        },
        "system_log": [
            {
                "level": "WARNING",
                "name": "custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai",
                "message": [
                    "Failed for 192.168.1.77 user jan.kowalski@example.com "
                    "serviceToken=LEAKED_STOKEN ssecurity=LEAKED_SSEC "
                    "Enc Key: LEAKED_ENC_KEY "
                    "https://api.io.mi.com/x?clientSign=LEAKED_SIGN&foo=1 "
                    "in /Users/jankowalski/config/custom_components"
                ],
                "source": ["vacuum_ijai.py", 42],
                "count": 3,
            },
            {
                "level": "ERROR",
                "name": "homeassistant.components.mqtt",
                "message": ["Unrelated MQTT error"],
                "source": ["mqtt.py", 1],
                "count": 1,
            },
        ],
    }


def _complete_report(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    report = core.build_report(
        raw or _sample_raw(),
        {"agent_name": "Claude Code", "agent_model": "claude-opus", "installation_type": "Home Assistant OS"},
    )
    report["problem"] = {
        "summary": "Ijai map fails to decrypt when map saving is off",
        "expected": "Live map displays the explored rooms",
        "actual": "Map shows MAP_SAVING_DISABLED and repair issue is raised",
        "reproduction": ["Install v1.0.0", "Configure xiaomi.vacuum.b106eu", "Trigger map update"],
        "diagnosis": "Diagnostics confirm remember-state returned 0 on the vacuum.",
        "workaround": "Enable map saving in Xiaomi Home.",
        "related": ["#9"],
    }
    report["testability"] = "device-specific"
    return report


def test_build_report_redacts_all_xiaomi_secrets_and_personal_data() -> None:
    report = _complete_report()
    dumped = json.dumps(report)

    # None of these secrets or personal identifiers may survive anywhere in the JSON.
    forbidden = [
        "192.168.1.77",
        "0123456789abcdef0123456789abcdef",
        "jan.kowalski@example.com",
        "SuperSecretPassword!",
        "AA:BB:CC:DD:EE:FF",
        "aabbccddeeff",
        "ssec-secret-value-xyz",
        "stoken-secret-value-xyz",
        "ptoken-secret-value-xyz",
        "deadbeef12345678",
        "SIG456",
        "AK123",
        "12345/6789012345678",
        "LEAKED_STOKEN",
        "LEAKED_SSEC",
        "LEAKED_ENC_KEY",
        "LEAKED_SIGN",
        "jankowalski",
        "kowalski_living_room",
        "Odkurzacz Jana",
        "dev-secret-uuid-12345",
        "01JABCDEF1234567890ABCDEF1",
        "abcdef1234567890",
        "sensor.unrelated_temp",
        "ignore_me",
        "Unrelated MQTT error",
    ]
    for secret in forbidden:
        assert secret not in dumped, f"secret or personal value leaked: {secret}"

    # Useful diagnostic data MUST survive.
    assert report["integration"]["version"] == "1.0.0"
    assert report["integration"]["original_installed"] is True
    assert report["vacuum"]["model"] == "xiaomi.vacuum.b106eu"
    assert report["vacuum"]["map_format"] == "IJAI"
    assert report["vacuum"]["server"] == "de"
    assert report["vacuum"]["map_status"] == "MAP_SAVING_DISABLED"
    assert report["vacuum"]["lan_reachable"] is False
    assert report["config_options"]["image_config"]["fit_to_explored"] is True
    assert "image.live_map" in report["states"]
    assert report["states"]["image.live_map"]["attributes"]["rooms"] == {"1": {"name": "Kitchen"}}
    assert len(report["repairs"]) == 1
    assert report["repairs"][0]["translation_key"] == "map_saving_disabled"
    assert len(report["logs"]["ha"]) == 1
    assert "cloud session store not collected" in report["redactions"]
    assert "map image not collected" in report["redactions"]


def test_render_and_review_complete_report() -> None:
    report = _complete_report()
    body = core.render_issue_body(report)
    assert core.MARKER in body
    assert core.DATA_START in body
    assert core.DATA_END in body
    assert "xiaomi.vacuum.b106eu" in body

    review = core.review_issue(body)
    assert review["isAgent"] is True
    assert review["complete"] is True
    assert review["error"] is None
    assert review["missing"] == []
    assert review["redactedBody"] is None
    assert review["secretHits"] == {}

    title = core.issue_title(report)
    assert title == "Ijai map fails to decrypt when map saving is off (xiaomi.vacuum.b106eu, 1.0.0)"


def test_review_detects_missing_fields_and_leaked_secrets() -> None:
    report = core.build_report(_sample_raw(), {})
    # Missing problem.* and agent.name
    missing = core.missing_fields(report)
    assert "agent.name" in missing
    assert "problem.summary" in missing

    leaked_body = (
        f"{core.MARKER}\n"
        "token=0123456789abcdef0123456789abcdef\n"
        "serviceToken=mySecretServiceToken123\n"
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevaluehere123\n"
    )
    review = core.review_issue(leaked_body)
    assert review["isAgent"] is True
    assert review["complete"] is False
    assert review["redactedBody"] is not None
    assert "0123456789abcdef0123456789abcdef" not in review["redactedBody"]
    assert "mySecretServiceToken123" not in review["redactedBody"]
    assert "signaturevaluehere123" not in review["redactedBody"]


def test_fit_report_trims_huge_payload_under_body_limit() -> None:
    report = _complete_report()
    report["diagnostics"]["device_data"]["huge_path"] = [{"x": i, "y": i} for i in range(4000)]
    rendered = core.render_issue_body(report)
    assert len(rendered) <= core.BODY_LIMIT
    parsed = core.parse_issue_body(rendered)
    assert parsed["error"] is None
    assert parsed["report"] is not None


def test_cli_collect_render_check_end_to_end(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    report_path = tmp_path / "report.json"
    issue_path = tmp_path / "issue.md"
    raw_path.write_text(json.dumps(_sample_raw()), encoding="utf-8")

    res = subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "collect",
            "--raw",
            str(raw_path),
            "--agent",
            "Claude Code",
            "--model",
            "claude-opus",
            "--out",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "collected: 1 entry(ies)" in res.stderr

    # Rendering before filling problem.* must fail cleanly.
    bad_render = subprocess.run(
        [sys.executable, str(CLI_PATH), "render", str(report_path)],
        capture_output=True,
        text=True,
    )
    assert bad_render.returncode != 0
    assert "problem.summary" in bad_render.stderr

    # Fill narrative fields and render + check.
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["problem"] = {
        "summary": "Map fails to decrypt",
        "expected": "Map renders",
        "actual": "MAP_SAVING_DISABLED",
        "reproduction": ["Install v1.0.0", "Trigger update"],
        "diagnosis": "Map saving is off.",
        "workaround": "",
        "related": [],
    }
    report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ok_render = subprocess.run(
        [sys.executable, str(CLI_PATH), "render", str(report_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "title: Map fails to decrypt" in ok_render.stderr
    issue_path.write_text(ok_render.stdout, encoding="utf-8")

    ok_check = subprocess.run(
        [sys.executable, str(CLI_PATH), "check", str(issue_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    checked = json.loads(ok_check.stdout)
    assert checked["complete"] is True
    assert checked["redactedBody"] is None


@pytest.mark.parametrize("body", ["Plain human issue without marker", ""])
def test_non_agent_issue_is_not_flagged_as_agent(body: str) -> None:
    review = core.review_issue(body)
    assert review["isAgent"] is False
    assert review["complete"] is False
    assert review["error"] is None
