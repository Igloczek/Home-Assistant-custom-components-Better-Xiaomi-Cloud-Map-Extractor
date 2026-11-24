"""Importing a vacuum set up in the original Xiaomi Cloud Map Extractor (`xiaomi_cloud_map_extractor`)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration

from custom_components.better_xiaomi_cloud_map_extractor import config_flow
from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN, ORIGINAL_DOMAIN

from .const import ENTRY_DATA, HOST, MAC, PASSWORD, TOKEN, USERNAME, VACUUM_DEVICE

DEVICE_ID = VACUUM_DEVICE["device_id"]
OPTIONS = {
    "image_config": XiaomiCloudMapExtractorFlowHandler._default_image_config(),
    "colors": XiaomiCloudMapExtractorFlowHandler._default_colors(),
    "room_colors": {"16": [10, 20, 30, 255]},
    "drawables": ["charger", "path", "room_names"],
    "sizes": {"charger_radius": 6},
    "texts": [],
}
# Shape written by store.save_connector_config (XiaomiCloudConnectorConfig); values are made up.
SESSION = {
    "connector_config": {
        "username": "user@example.com",
        "password": "secret",
        "server": "de",
        "user_id": "2",
        "c_user_id": "c2",
        "service_token": "made-up-service-token",
        "expiration": "2030-01-01T00:00:00+00:00",
        "ssecurity": "made-up-ssecurity",
        "device_id": "ABCDEFGHIJKLMNOP",
    }
}


def _original_setup(hass: HomeAssistant, hass_storage: dict[str, Any]) -> MockConfigEntry:
    """What the original v3.0.0-alpha-24 leaves behind once it is removed from HACS.

    Entity unique ids are `<device_id>_<entity domain>_<key>` (entity.py), the same in both
    integrations; only the registry platform differs.
    """
    original = MockConfigEntry(
        domain=ORIGINAL_DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS
    )
    original.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=original.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
        identifiers={(ORIGINAL_DOMAIN, DEVICE_ID)},
        name="Living room vacuum",
    )
    devices.async_update_device(device.id, name_by_user="Robot")
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "image",
        ORIGINAL_DOMAIN,
        f"{DEVICE_ID}_image_live_map",
        config_entry=original,
        device_id=device.id,
        suggested_object_id="living_room_vacuum_live_map",
    )
    renamed = registry.async_get_or_create(
        "sensor",
        ORIGINAL_DOMAIN,
        f"{DEVICE_ID}_sensor_vacuum_room_name",
        config_entry=original,
        suggested_object_id="living_room_vacuum_vacuum_room_name",
    )
    registry.async_update_entity(renamed.entity_id, new_entity_id="sensor.robot_room")
    hass_storage[f"{ORIGINAL_DOMAIN}_{MAC}"] = {"version": 1, "key": f"{ORIGINAL_DOMAIN}_{MAC}", "data": SESSION}
    return original


async def _import_from_menu(hass: HomeAssistant, original: MockConfigEntry) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["import_original", "auth_credentials", "auth_qr"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})
    assert result["step_id"] == "import_original"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"original_entry": original.entry_id})


async def test_import_keeps_settings_session_and_entity_ids(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock
) -> None:
    original = _original_setup(hass, hass_storage)

    result = await _import_from_menu(hass, original)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Living room vacuum"
    assert entry.unique_id == MAC
    assert dict(entry.data) == ENTRY_DATA
    assert dict(entry.options) == OPTIONS
    assert hass_storage[f"{DOMAIN}_{MAC}"]["data"] == SESSION

    registry = er.async_get(hass)
    image = registry.async_get("image.living_room_vacuum_live_map")
    assert (image.platform, image.config_entry_id, image.unique_id) == (
        DOMAIN,
        entry.entry_id,
        f"{DEVICE_ID}_image_live_map",
    )
    assert hass.states.get("image.living_room_vacuum_live_map") is not None
    device = dr.async_get(hass).async_get(image.device_id)
    assert device.name_by_user == "Robot"
    assert device.config_entry_id == entry.entry_id
    assert device.identifiers == {(DOMAIN, DEVICE_ID)}
    assert not [e for e in registry.entities.values() if e.entity_id.endswith("_2")]
    room = registry.async_get("sensor.robot_room")
    assert (room.platform, room.config_entry_id) == (DOMAIN, entry.entry_id)

    # The original entry and its session file are left as they were.
    assert hass.config_entries.async_get_entry(original.entry_id) is original
    assert dict(original.data) == ENTRY_DATA
    assert hass_storage[f"{ORIGINAL_DOMAIN}_{MAC}"]["data"] == SESSION

    # Deleting the leftover original entry afterwards keeps everything that moved.
    assert await hass.config_entries.async_remove(original.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("image.living_room_vacuum_live_map").config_entry_id == entry.entry_id
    assert registry.async_get("sensor.robot_room") is not None
    assert dr.async_get(hass).async_get(image.device_id).name_by_user == "Robot"


async def test_original_still_installed_changes_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock
) -> None:
    original = _original_setup(hass, hass_storage)
    mock_integration(hass, MockModule(ORIGINAL_DOMAIN))

    result = await _import_from_menu(hass, original)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "original_installed"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert f"{DOMAIN}_{MAC}" not in hass_storage
    image = er.async_get(hass).async_get("image.living_room_vacuum_live_map")
    assert (image.platform, image.config_entry_id) == (ORIGINAL_DOMAIN, original.entry_id)


async def test_entities_stay_with_the_original_while_it_is_installed(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock
) -> None:
    """A vacuum set up in both integrations: setup must not take the original's entities."""
    original = _original_setup(hass, hass_storage)
    mock_integration(hass, MockModule(ORIGINAL_DOMAIN))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    image = er.async_get(hass).async_get("image.living_room_vacuum_live_map")
    assert (image.platform, image.config_entry_id) == (ORIGINAL_DOMAIN, original.entry_id)
    assert hass.states.get("image.living_room_vacuum_live_map_2") is not None


async def test_menu_has_no_import_without_original_entries(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["menu_options"] == ["auth_credentials", "auth_qr"]


async def test_remaining_original_vacuums_are_discovered(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock
) -> None:
    """Once the integration runs, vacuums left in the original show up under Discovered."""
    _original_setup(hass, hass_storage)
    second = MockConfigEntry(
        domain=ORIGINAL_DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="Upstairs",
        data={**ENTRY_DATA, "mac": "11:22:33:44:55:66", "device_id": "987"},
        options=OPTIONS,
    )
    second.add_to_hass(hass)
    imported = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=ENTRY_DATA, options=OPTIONS)
    imported.add_to_hass(hass)

    assert await hass.config_entries.async_setup(imported.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [(f["context"]["source"], f["context"]["unique_id"], f["step_id"]) for f in flows] == [
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, "11:22:33:44:55:66", "import_original_confirm")
    ]

    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Upstairs"


@pytest.mark.parametrize("unique_id", [None])
async def test_original_entry_without_unique_id_is_not_offered(
    hass: HomeAssistant, unique_id: str | None
) -> None:
    """v2 YAML imports in the original have no unique id; they are set up again from YAML instead."""
    MockConfigEntry(domain=ORIGINAL_DOMAIN, unique_id=unique_id, data=ENTRY_DATA, options=OPTIONS).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["menu_options"] == ["auth_credentials", "auth_qr"]


async def test_v2_yaml_with_the_new_platform_name_is_imported(
    hass: HomeAssistant, map_connector: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`camera: platform: better_xiaomi_cloud_map_extractor` replaces the original's v2 YAML."""
    monkeypatch.setattr(
        config_flow, "create_config_entry_data_from_yaml", AsyncMock(return_value=(ENTRY_DATA, OPTIONS))
    )
    yaml = {"platform": DOMAIN, "host": HOST, "token": TOKEN, "username": USERNAME, "password": PASSWORD}

    assert await async_setup_component(hass, "camera", {"camera": [yaml]})
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert dict(entry.data) == ENTRY_DATA
    config_flow.create_config_entry_data_from_yaml.assert_awaited_once()
