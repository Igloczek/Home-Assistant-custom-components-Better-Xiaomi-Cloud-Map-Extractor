"""Importing a vacuum set up in the original Xiaomi Cloud Map Extractor (`xiaomi_cloud_map_extractor`).

Every test runs over each shape of original entry alpha-24 leaves behind (tests/original_entries.py):
set up in its UI flow (unique_id = MAC), imported from v2 YAML (no unique_id, MAC in data), and
imported from v2 YAML when the cloud lookup failed (no MAC at all).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration

from custom_components.better_xiaomi_cloud_map_extractor import config_flow
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN, ORIGINAL_DOMAIN

from .const import ENTRY_DATA, HOST, MAC, PASSWORD, TOKEN, USERNAME
from .original_entries import (
    ALPHA24_ENTITIES,
    ENABLED,
    OPTIONS,
    SESSION,
    YAML,
    YAML_WITHOUT_MAC,
    OriginalShape,
    add_original,
)

OTHER_MAC = "11:22:33:44:55:66"
OTHER_HOST = "192.168.1.77"


async def _import_from_menu(hass: HomeAssistant, original: MockConfigEntry) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["import_original", "auth_credentials", "auth_qr"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})
    assert result["step_id"] == "import_original"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"original_entry": original.entry_id})


async def _menu_options(hass: HomeAssistant) -> list[str]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    hass.config_entries.flow.async_abort(result["flow_id"])
    return result["menu_options"]


def _entity_id(shape: OriginalShape, domain: str, key: str) -> str:
    if (domain, key) == ("sensor", "vacuum_room_name"):
        return "sensor.robot_room"
    return f"{domain}.living_room_vacuum_{key}"


async def test_import_keeps_settings_session_and_entity_ids(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, original_with_mac: OriginalShape
) -> None:
    shape = original_with_mac
    original = add_original(hass, hass_storage, shape)

    result = await _import_from_menu(hass, original)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == shape.title
    assert entry.unique_id == MAC
    assert dict(entry.data) == shape.data
    assert dict(entry.options) == OPTIONS
    assert entry.state is ConfigEntryState.LOADED
    if shape.has_session:
        assert hass_storage[f"{DOMAIN}_{MAC}"]["data"] == SESSION
    else:
        assert f"{DOMAIN}_{MAC}" not in hass_storage

    # All 26 entities moved with their entity ids and enabled state; none was created twice.
    registry = er.async_get(hass)
    for domain, key in ALPHA24_ENTITIES:
        moved = registry.async_get(_entity_id(shape, domain, key))
        assert (moved.platform, moved.config_entry_id, moved.unique_id) == (
            DOMAIN,
            entry.entry_id,
            f"{shape.device_id}_{domain}_{key}",
        )
        assert (moved.disabled_by is None) == ((domain, key) in ENABLED), moved.entity_id
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == len(ALPHA24_ENTITIES) == 26
    assert er.async_entries_for_config_entry(registry, original.entry_id) == []
    assert hass.states.get("image.living_room_vacuum_live_map") is not None

    image = registry.async_get("image.living_room_vacuum_live_map")
    device = dr.async_get(hass).async_get(image.device_id)
    assert device.name_by_user == "Robot"
    assert device.config_entry_id == entry.entry_id
    assert device.identifiers == {(DOMAIN, shape.device_id)}
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1

    # The original entry and its session file are left as they were.
    assert hass.config_entries.async_get_entry(original.entry_id) is original
    assert dict(original.data) == shape.data
    assert original.unique_id == shape.unique_id
    if shape.has_session:
        assert hass_storage[f"{ORIGINAL_DOMAIN}_{MAC}"]["data"] == SESSION

    # Nothing is left to import.
    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]

    # Deleting the leftover original entry afterwards keeps everything that moved.
    assert await hass.config_entries.async_remove(original.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("image.living_room_vacuum_live_map").config_entry_id == entry.entry_id
    assert registry.async_get("sensor.robot_room") is not None
    assert dr.async_get(hass).async_get(image.device_id).name_by_user == "Robot"


async def test_import_without_mac_asks_to_log_in_once(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    map_connector: MagicMock,
    cloud_connector: MagicMock,
    no_store: AsyncMock,
) -> None:
    """The original's YAML import found no vacuum in the cloud, so the entry has no MAC or device id.

    It is offered and imported as it is; setup then asks to log in (reauth, one click with the
    stored credentials), which fills in the MAC, the device id and the unique id.
    """
    original = add_original(hass, hass_storage, YAML_WITHOUT_MAC)

    result = await _import_from_menu(hass, original)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id is None
    assert dict(entry.data) == YAML_WITHOUT_MAC.data
    assert entry.state is ConfigEntryState.SETUP_ERROR
    [reauth] = entry.async_get_active_flows(hass, {config_entries.SOURCE_REAUTH})
    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]

    result = await hass.config_entries.flow.async_configure(reauth["flow_id"], {})
    assert result["menu_options"][0] == "auth_saved_credentials"
    result = await hass.config_entries.flow.async_configure(
        reauth["flow_id"], {"next_step_id": "auth_saved_credentials"}
    )
    cloud_connector.login_with_credentials.assert_awaited_once_with(USERNAME, PASSWORD)
    result = await hass.config_entries.flow.async_configure(
        reauth["flow_id"], {"host": HOST, "token": TOKEN, "used_map_api": "VIOMI"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == MAC
    assert entry.data == {**ENTRY_DATA, "used_map_api": "VIOMI"}
    assert entry.state is ConfigEntryState.LOADED
    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]
    assert hass.config_entries.async_get_entry(original.entry_id).data == YAML_WITHOUT_MAC.data


async def test_original_still_installed_changes_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, original_shape: OriginalShape
) -> None:
    original = add_original(hass, hass_storage, original_shape)
    mock_integration(hass, MockModule(ORIGINAL_DOMAIN))

    result = await _import_from_menu(hass, original)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "original_installed"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert f"{DOMAIN}_{MAC}" not in hass_storage
    if original_shape.has_entities:
        image = er.async_get(hass).async_get("image.living_room_vacuum_live_map")
        assert (image.platform, image.config_entry_id) == (ORIGINAL_DOMAIN, original.entry_id)


async def test_entities_stay_with_the_original_while_it_is_installed(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, original_with_mac: OriginalShape
) -> None:
    """A vacuum set up in both integrations: setup must not take the original's entities."""
    original = add_original(hass, hass_storage, original_with_mac)
    mock_integration(hass, MockModule(ORIGINAL_DOMAIN))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=original_with_mac.data, options=OPTIONS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    image = er.async_get(hass).async_get("image.living_room_vacuum_live_map")
    assert (image.platform, image.config_entry_id) == (ORIGINAL_DOMAIN, original.entry_id)
    assert hass.states.get("image.living_room_vacuum_live_map_2") is not None


async def test_menu_has_no_import_without_original_entries(hass: HomeAssistant) -> None:
    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]


async def test_remaining_original_vacuums_are_discovered(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    map_connector: MagicMock,
    original_shape: OriginalShape,
) -> None:
    """Once the integration runs, vacuums left in the original show up under Discovered."""
    add_original(hass, hass_storage, original_shape)
    imported = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_MAC,
        data={**ENTRY_DATA, "host": OTHER_HOST, "mac": OTHER_MAC, "device_id": "987"},
        options=OPTIONS,
    )
    imported.add_to_hass(hass)

    assert await hass.config_entries.async_setup(imported.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [(f["context"]["source"], f["context"].get("unique_id"), f["step_id"]) for f in flows] == [
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, original_shape.mac, "import_original_confirm")
    ]

    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == original_shape.title
    assert result["result"].unique_id == original_shape.mac
    assert dict(result["result"].data) == original_shape.data
    if original_shape.has_entities:
        image = er.async_get(hass).async_get("image.living_room_vacuum_live_map")
        assert (image.platform, image.config_entry_id) == (DOMAIN, result["result"].entry_id)


async def test_already_imported_original_is_not_offered_or_discovered(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, original_shape: OriginalShape
) -> None:
    add_original(hass, hass_storage, original_shape)
    # What the import creates: a copy of the original with its MAC (if any) as unique id.
    MockConfigEntry(domain=DOMAIN, unique_id=original_shape.mac, data=original_shape.data).add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]
    discovered = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY}
    )
    assert discovered == []


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


async def test_v2_yaml_with_the_new_platform_name_takes_over_the_originals_yaml_entry(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user renamed the YAML platform instead of using the import: one entry, same entity ids."""
    original = add_original(hass, hass_storage, YAML)
    monkeypatch.setattr(
        config_flow, "create_config_entry_data_from_yaml", AsyncMock(return_value=(YAML.data, OPTIONS))
    )
    yaml = {"platform": DOMAIN, "host": HOST, "token": TOKEN, "username": USERNAME, "password": PASSWORD}

    assert await async_setup_component(hass, "camera", {"camera": [yaml]})
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    image = registry.async_get("image.living_room_vacuum_live_map")
    assert (image.platform, image.config_entry_id) == (DOMAIN, entry.entry_id)
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 26
    assert er.async_entries_for_config_entry(registry, original.entry_id) == []
    assert await _menu_options(hass) == ["auth_credentials", "auth_qr"]


async def test_v2_yaml_for_an_imported_vacuum_does_not_create_a_second_entry(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Imported from the original, then the YAML platform renamed too: no second entry, no login."""
    original = add_original(hass, hass_storage, YAML)
    await _import_from_menu(hass, original)
    await hass.async_block_till_done()
    monkeypatch.setattr(
        config_flow, "create_config_entry_data_from_yaml", AsyncMock(return_value=(YAML.data, OPTIONS))
    )
    yaml = {"platform": DOMAIN, "host": HOST, "token": TOKEN, "username": USERNAME, "password": PASSWORD}

    assert await async_setup_component(hass, "camera", {"camera": [yaml]})
    await hass.async_block_till_done()

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    config_flow.create_config_entry_data_from_yaml.assert_not_awaited()


async def test_originals_imported_from_yaml_are_told_apart_by_host(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The original titles every YAML-imported entry "Xiaomi Cloud Map Extractor"."""
    add_original(hass, hass_storage, YAML)
    second = MockConfigEntry(
        domain=ORIGINAL_DOMAIN,
        source=config_entries.SOURCE_IMPORT,
        title=YAML.title,
        data={**YAML.data, "host": OTHER_HOST, "mac": OTHER_MAC, "device_id": "987"},
        options=OPTIONS,
    )
    second.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})

    [selector] = result["data_schema"].schema.values()
    assert sorted(option["label"] for option in selector.config["options"]) == [
        f"Xiaomi Cloud Map Extractor ({HOST})",
        f"Xiaomi Cloud Map Extractor ({OTHER_HOST})",
    ]
