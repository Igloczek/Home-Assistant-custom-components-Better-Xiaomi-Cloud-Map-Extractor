"""Shared fixtures.

The Xiaomi cloud and the vacuum are never contacted: the config flow talks to a mocked
`XiaomiCloudConnector`, and setup uses a mocked `XiaomiCloudMapExtractorConnector`.
"""

from __future__ import annotations

import json
import string
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowManager, FlowResultType
from homeassistant import block_async_io

from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
    XiaomiCloudMapExtractorData,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import (
    XiaomiCloudDeviceInfo,
)

from .const import VACUUM_DEVICE
from .original_entries import ALL_SHAPES, SHAPES_WITH_MAC, OriginalShape


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


TRANSLATIONS = json.loads(
    (Path(__file__).parents[1] / "custom_components/better_xiaomi_cloud_map_extractor/translations/en.json").read_text()
)
# Reasons Home Assistant translates itself.
COMMON_ABORTS = {"already_configured", "already_in_progress"}


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def assert_renders(texts: dict, result: dict) -> None:
    """The frontend can show this flow result: every text exists and gets each placeholder it uses.

    A missing placeholder shows as `MISSING_VALUE` in the frontend (the QR login step, upstream #795).
    """
    texts = {"abort": {}, "error": {}, "step": {}, "progress": {}, **texts}
    kind, step_id = result["type"], result.get("step_id")
    provided = set(result.get("description_placeholders") or {})
    if kind is FlowResultType.ABORT:
        reason = result["reason"]
        assert reason in COMMON_ABORTS or reason in texts["abort"], f"abort reason {reason!r} is not translated"
        used = [texts["abort"].get(reason, "")]
    elif kind is FlowResultType.SHOW_PROGRESS:
        used = [texts["progress"][result["progress_action"]]]
    elif kind in (FlowResultType.FORM, FlowResultType.MENU):
        step = texts["step"].get(step_id)
        assert step is not None, f"step {step_id!r} is not translated"
        used = [step.get("title", ""), step.get("description", "")]
        for error in (result.get("errors") or {}).values():
            assert error in texts["error"], f"error {error!r} of step {step_id!r} is not translated"
        for option in result.get("menu_options") or []:
            assert option in step.get("menu_options", {}), f"menu option {option!r} is not translated"
        # A form field without a label shows its raw key.
        fields = result.get("data_schema") if kind is FlowResultType.FORM else None
        for field in (fields or vol.Schema({})).schema:
            if step_id == "room_colors_step2" and str(field).isdigit():
                continue  # one field per room, labelled with the room id itself
            assert str(field) in step.get("data", {}), f"field {str(field)!r} of step {step_id!r} has no label"
    else:
        return
    missing = set().union(*(_placeholders(text) for text in used)) - provided
    assert not missing, f"{kind} {step_id or result.get('reason')!r} lacks placeholders {sorted(missing)}"


@pytest.fixture(autouse=True)
def flow_results_render() -> Generator[None]:
    """Checks every config, options and repair flow result of this integration against translations/en.json."""
    from homeassistant.components.repairs import RepairsFlow
    from homeassistant.helpers import issue_registry as ir

    from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN
    from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
    from custom_components.better_xiaomi_cloud_map_extractor.options_flow import (
        XiaomiCloudMapExtractorOptionsFlowHandler,
    )

    handle_step = FlowManager._async_handle_step

    async def checked(self, flow, step_id, user_input):
        result = await handle_step(self, flow, step_id, user_input)
        if isinstance(flow, XiaomiCloudMapExtractorOptionsFlowHandler):
            assert_renders(TRANSLATIONS["options"], result)
        elif isinstance(flow, XiaomiCloudMapExtractorFlowHandler):
            assert_renders(TRANSLATIONS["config"], result)
        elif isinstance(flow, RepairsFlow) and flow.handler == DOMAIN:
            issue = ir.async_get(flow.hass).async_get_issue(DOMAIN, flow.issue_id)
            if issue is not None and result["type"] is not FlowResultType.CREATE_ENTRY:
                fix_flow = TRANSLATIONS["issues"].get(issue.translation_key, {}).get("fix_flow")
                assert fix_flow is not None, f"issue {issue.translation_key!r} has no fix_flow texts"
                assert_renders(fix_flow, result)
        return result

    with patch.object(FlowManager, "_async_handle_step", checked):
        yield


@pytest.fixture(autouse=True)
def issues_render() -> Generator[None]:
    """Every Repairs issue this integration raises is translated and gets each placeholder it uses.

    Issues were not checked like flow results are; a missing placeholder shows a raw `{name}` in
    Repairs.
    """
    from homeassistant.helpers.issue_registry import IssueRegistry

    from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

    get_or_create = IssueRegistry.async_get_or_create

    def checked(self, domain, issue_id, *args, **kwargs):
        if domain == DOMAIN:
            key = kwargs["translation_key"]
            texts = TRANSLATIONS["issues"].get(key)
            assert texts is not None, f"issue {key!r} is not translated"
            used = [texts.get("title", ""), texts.get("description", "")]
            for step in texts.get("fix_flow", {}).get("step", {}).values():
                used += [step.get("title", ""), step.get("description", "")]
            provided = set(kwargs.get("translation_placeholders") or {})
            missing = set().union(*(_placeholders(text) for text in used)) - provided
            assert not missing, f"issue {key!r} lacks placeholders {sorted(missing)}"
        return get_or_create(self, domain, issue_id, *args, **kwargs)

    with patch.object(IssueRegistry, "async_get_or_create", checked):
        yield


@pytest.fixture(autouse=True)
def detect_blocking_calls() -> Generator[None]:
    """Home Assistant's "Detected blocking call" check, as a running instance has it.

    Only `bootstrap` turns it on, so the test harness runs without it. Here a blocking call
    (`time.sleep`, `open`, ...) made on the event loop from the integration raises, like it
    logs on a real installation. `tests/fake_miio.py` blocks like a LAN round trip does.
    """
    block_async_io.enable()
    yield
    for blocked in block_async_io._BLOCKED_CALLS.calls:
        setattr(blocked.object, blocked.function, blocked.original_func)
    block_async_io._BLOCKED_CALLS.calls.clear()


def make_device(**overrides) -> XiaomiCloudDeviceInfo:
    return XiaomiCloudDeviceInfo(**{**VACUUM_DEVICE, **overrides})


@pytest.fixture
def cloud_connector() -> Generator[MagicMock]:
    """The cloud connector the config flow creates, logged in with one vacuum."""
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.flow.login.XiaomiCloudConnector",
        autospec=True,
    ) as connector_class:
        connector = connector_class.return_value
        connector.login_with_credentials = AsyncMock(return_value="user_id")
        connector.get_devices = AsyncMock(return_value=[make_device()])
        connector.to_config = MagicMock(return_value=None)
        # The miIO command forwarding (`/v2/home/rpc/<did>`) answers like the real cloud does for a
        # vacuum it cannot reach, never with a mock that passes for any reply; tests opt in to an answer.
        connector.get_other_info = AsyncMock(return_value={"code": -2, "message": "device offline"})
        yield connector


@pytest.fixture
def no_store() -> Generator[AsyncMock]:
    """Skip persisting the cloud session; the flow tests do not read it back."""
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.flow.vacuum.save_connector_config",
        new=AsyncMock(),
    ) as save:
        yield save


@pytest.fixture
def map_connector() -> Generator[MagicMock]:
    """The runtime connector used by the coordinator; returns an empty, healthy map."""
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.XiaomiCloudMapExtractorConnector",
        autospec=True,
    ) as connector_class:
        connector = connector_class.return_value
        connector.get_data = AsyncMock(
            return_value=XiaomiCloudMapExtractorData(status=XiaomiCloudMapExtractorConnectorStatus.OK)
        )
        connector.is_auto_updating = MagicMock(return_value=True)
        yield connector


@pytest.fixture(params=ALL_SHAPES, ids=lambda shape: shape.name)
def original_shape(request: pytest.FixtureRequest) -> OriginalShape:
    """Every shape of entry the original integration leaves behind (tests/original_entries.py)."""
    return request.param


@pytest.fixture(params=SHAPES_WITH_MAC, ids=lambda shape: shape.name)
def original_with_mac(request: pytest.FixtureRequest) -> OriginalShape:
    """Original entries that know their vacuum's MAC, so they have entities and a device."""
    return request.param
