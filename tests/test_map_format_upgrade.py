"""A vacuum added while its model was unsupported gets a one-click repair once a release reads it.

The config flow stores the detected map format, `UNSUPPORTED` for a model the integration could not
read yet. A later release that adds the model's format left those entries on `UNSUPPORTED` for good:
the map status is OK, so no failure repair fired, and there is no reconfigure flow.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.better_xiaomi_cloud_map_extractor as integration
from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus as Status,
    XiaomiCloudMapExtractorData,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedMapDownloadException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumApi
from custom_components.better_xiaomi_cloud_map_extractor.const import CONF_USED_MAP_API, DOMAIN

from .const import ENTRY_DATA, MAC

# Supported today; stands in for a model whose format a release has just added (XTL in PR #28).
IJAI = "ijai.vacuum.v19"


def _entry(hass: HomeAssistant, model: str = IJAI, used_api: str | None = "UNSUPPORTED") -> MockConfigEntry:
    flow = XiaomiCloudMapExtractorFlowHandler
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Living room vacuum",
        data={**ENTRY_DATA, "model": model, CONF_USED_MAP_API: used_api},
        options={
            "image_config": flow._default_image_config(),
            "colors": flow._default_colors(),
            "room_colors": {},
            "drawables": ["charger", "path"],
            "sizes": {},
            "texts": [],
        },
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"map_format_upgrade_{entry.entry_id}")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _fix(hass: HomeAssistant, issue: ir.IssueEntry) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["detected_api"] == "Ijai"
    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unsupported_entry_gets_the_new_format_in_one_confirm(
    hass: HomeAssistant, map_connector: MagicMock
) -> None:
    """Setup → fixable issue → one confirm → entry uses Ijai, is reloaded, issue gone, rest kept."""
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_key == "map_format_upgrade"
    assert issue.translation_placeholders == {
        "name": "Living room vacuum",
        "model": IJAI,
        "detected_api": "Ijai",
    }
    # The wrong-format repair is about a failing map; nothing failed here.
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"wrong_map_format_{entry.entry_id}") is None

    connector_class = integration.XiaomiCloudMapExtractorConnector  # patched by map_connector
    await _fix(hass, issue)

    assert entry.data[CONF_USED_MAP_API] == "IJAI"
    assert entry.state is ConfigEntryState.LOADED
    used_apis = [call.args[1].used_api for call in connector_class.call_args_list]
    assert used_apis[0] is VacuumApi.UNSUPPORTED
    assert used_apis[-1] is VacuumApi.IJAI
    assert _issue(hass, entry) is None
    assert {k: v for k, v in entry.data.items() if k != CONF_USED_MAP_API} == {
        k: v for k, v in {**ENTRY_DATA, "model": IJAI}.items() if k != CONF_USED_MAP_API
    }


async def test_issue_is_raised_and_fixed_while_setup_is_retrying(
    hass: HomeAssistant, map_connector: MagicMock
) -> None:
    """Raised at setup, before the first map update, so a failing update can't hide it."""
    map_connector.get_data.side_effect = FailedMapDownloadException()
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY

    issue = _issue(hass, entry)
    assert issue is not None

    map_connector.get_data.side_effect = None
    map_connector.get_data.return_value = XiaomiCloudMapExtractorData(status=Status.OK)
    await _fix(hass, issue)

    assert entry.data[CONF_USED_MAP_API] == "IJAI"
    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, entry) is None


async def test_issue_is_logged_once(
    hass: HomeAssistant, map_connector: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    lines = [r for r in caplog.records if r.levelno >= logging.WARNING and IJAI in r.getMessage()]
    assert len(lines) == 1
    assert "Ijai" in lines[0].getMessage()


@pytest.mark.parametrize(
    ("model", "used_api"),
    [
        # Still no format for this model: nothing to offer.
        ("mijia.vacuum.m40pro", "UNSUPPORTED"),
        # Already on its format.
        (IJAI, "IJAI"),
        # The YAML import stores no format: it is detected at every setup.
        (IJAI, None),
        # A format chosen on purpose, even if another is detected (the wrong-format repair covers it).
        (IJAI, "XIAOMI"),
    ],
)
async def test_no_issue_without_a_new_format(
    hass: HomeAssistant, map_connector: MagicMock, model: str, used_api: str | None
) -> None:
    entry = _entry(hass, model, used_api)
    await _setup(hass, entry)

    assert _issue(hass, entry) is None
    assert entry.data[CONF_USED_MAP_API] == used_api


async def test_issue_is_cleared_once_the_entry_has_a_format(hass: HomeAssistant, map_connector: MagicMock) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _issue(hass, entry) is not None

    # Changed some other way (for example a new entry data written by a future flow): reloads.
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_USED_MAP_API: "IJAI"})
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


async def test_issue_is_removed_with_the_entry(hass: HomeAssistant, map_connector: MagicMock) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _issue(hass, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, entry) is None
