"""A vacuum whose map fails in the Xiaomi format gets a one-click repair to its own format.

Upstream #731, #763 and #784: Roborock vacuums set to the Xiaomi map format never got a map and
the only hint was in the log. The repair appears only after a real failure, so a vacuum whose map
works in the Xiaomi format is never touched.
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
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumApi
from custom_components.better_xiaomi_cloud_map_extractor.const import CONF_USED_MAP_API, DOMAIN

from .const import ENTRY_DATA, MAC

ROBOROCK = "roborock.vacuum.s5e"


def _entry(hass: HomeAssistant, model: str = ROBOROCK, used_api: str = "XIAOMI") -> MockConfigEntry:
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


def _status(map_connector: MagicMock, status: Status) -> None:
    map_connector.get_data.return_value = XiaomiCloudMapExtractorData(status=status)


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"wrong_map_format_{entry.entry_id}")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_failed_xiaomi_map_offers_the_detected_format_and_the_fix_switches_it(
    hass: HomeAssistant, map_connector: MagicMock
) -> None:
    """Failure → fixable issue → one confirm → entry uses Roborock, is reloaded, issue gone."""
    _status(map_connector, Status.FAILED_MAP_PARSING)
    entry = _entry(hass)
    await _setup(hass, entry)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_key == "wrong_map_format"
    assert issue.translation_placeholders == {
        "name": "Living room vacuum",
        "model": ROBOROCK,
        "used_api": "Xiaomi",
        "detected_api": "Roborock",
    }

    _status(map_connector, Status.OK)
    connector_class = integration.XiaomiCloudMapExtractorConnector  # patched by map_connector
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["detected_api"] == "Roborock"

    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_USED_MAP_API] == "ROBOROCK"
    assert entry.state is ConfigEntryState.LOADED
    used_apis = [call.args[1].used_api for call in connector_class.call_args_list]
    assert used_apis[0] is VacuumApi.XIAOMI
    assert used_apis[-1] is VacuumApi.ROBOROCK
    assert _issue(hass, entry) is None
    # Everything else in the entry is kept.
    assert {k: v for k, v in entry.data.items() if k != CONF_USED_MAP_API} == {
        k: v for k, v in {**ENTRY_DATA, "model": ROBOROCK}.items() if k != CONF_USED_MAP_API
    }


async def test_issue_is_cleared_when_the_map_works_again(hass: HomeAssistant, map_connector: MagicMock) -> None:
    _status(map_connector, Status.FAILED_MAP_PARSING)
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _issue(hass, entry) is not None

    _status(map_connector, Status.OK)
    await entry.runtime_data.coordinator.async_refresh()

    assert _issue(hass, entry) is None


async def test_failure_is_logged_once(
    hass: HomeAssistant, map_connector: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    _status(map_connector, Status.FAILED_MAP_PARSING)
    entry = _entry(hass)
    await _setup(hass, entry)
    await entry.runtime_data.coordinator.async_refresh()
    await entry.runtime_data.coordinator.async_refresh()

    lines = [r for r in caplog.records if r.levelno >= logging.WARNING and ROBOROCK in r.getMessage()]
    assert len(lines) == 1
    assert "Roborock" in lines[0].getMessage()


@pytest.mark.parametrize(
    ("model", "used_api", "status"),
    [
        # The Xiaomi format works for this Roborock: leave it alone.
        (ROBOROCK, "XIAOMI", Status.OK),
        # A Xiaomi model failing in its own format: nothing better to offer.
        ("xiaomi.vacuum.c107", "XIAOMI", Status.FAILED_MAP_PARSING),
        # Unknown vendor: no detected format to switch to.
        ("mijia.vacuum.m40pro", "XIAOMI", Status.FAILED_MAP_PARSING),
        # The download failed, not the format.
        (ROBOROCK, "XIAOMI", Status.FAILED_MAP_DOWNLOAD),
        # Already on the detected format.
        (ROBOROCK, "ROBOROCK", Status.FAILED_MAP_PARSING),
    ],
)
async def test_no_issue_without_a_real_format_mismatch(
    hass: HomeAssistant, map_connector: MagicMock, model: str, used_api: str, status: Status
) -> None:
    _status(map_connector, status)
    entry = _entry(hass, model, used_api)
    await _setup(hass, entry)

    assert _issue(hass, entry) is None
    assert entry.data[CONF_USED_MAP_API] == used_api


async def test_issue_is_removed_with_the_entry(hass: HomeAssistant, map_connector: MagicMock) -> None:
    _status(map_connector, Status.FAILED_MAP_PARSING)
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _issue(hass, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, entry) is None
