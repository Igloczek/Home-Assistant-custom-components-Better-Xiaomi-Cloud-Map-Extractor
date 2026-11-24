"""Every entry for a vacuum whose MAC is known gets that MAC (`format_mac`) as unique id.

Without it Home Assistant can't tell the vacuum is already set up (duplicates in the user flow and
discovery), and reauth can't check it's the same vacuum. Entries are created by: the import from
the original (menu or Discovered, original installed and loaded), the v2 YAML import, and earlier
versions that stored none.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor import config_flow
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

from .const import HOST, MAC, PASSWORD, TOKEN, USERNAME
from .original_entries import OPTIONS, YAML, add_original, install_original

# The cloud reports MACs in upper case; how a stored one is written must not matter.
YAML_UPPER_MAC = dataclasses.replace(YAML, data={**YAML.data, "mac": MAC.upper()})


@pytest.mark.parametrize("shape", [YAML, YAML_UPPER_MAC], ids=["mac", "upper_case_mac"])
@pytest.mark.parametrize("path", ["menu", "discovered"])
async def test_import_of_a_yaml_made_original_while_installed_gets_the_mac(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, shape, path: str
) -> None:
    original = add_original(hass, hass_storage, shape)
    install_original(hass)
    await hass.config_entries.async_setup(original.entry_id)
    await hass.async_block_till_done()

    if path == "menu":
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"original_entry": original.entry_id})
    else:
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.unique_id == MAC


async def test_v2_yaml_import_gets_the_mac(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`camera: platform: better_xiaomi_cloud_map_extractor` next to the original's YAML-made entry."""
    add_original(hass, hass_storage, YAML_UPPER_MAC)
    monkeypatch.setattr(
        config_flow, "create_config_entry_data_from_yaml", AsyncMock(return_value=(YAML_UPPER_MAC.data, OPTIONS))
    )
    yaml = {"platform": DOMAIN, "host": HOST, "token": TOKEN, "username": USERNAME, "password": PASSWORD}

    assert await async_setup_component(hass, "camera", {"camera": [yaml]})
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.unique_id == MAC


async def test_setup_fills_in_a_missing_unique_id_from_the_stored_mac(
    hass: HomeAssistant, map_connector: MagicMock
) -> None:
    """Entries already imported by 0.1.0 without a unique id get it at the next start."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=None, data=YAML_UPPER_MAC.data, options=OPTIONS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.unique_id == MAC


async def test_setup_leaves_the_unique_id_empty_when_another_entry_has_the_mac(
    hass: HomeAssistant, map_connector: MagicMock
) -> None:
    other = MockConfigEntry(domain=DOMAIN, unique_id=MAC, source=config_entries.SOURCE_IGNORE, data={})
    other.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=None, data=YAML.data, options=OPTIONS)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.unique_id is None
    assert other.unique_id == MAC
