"""Every step of the options flow, driven to the end through Home Assistant.

Each step is opened from the menu and submitted with what the form suggests, for the options
v3.0.0-alpha-24 (688fe3e) writes (stored colours are RGBA), so a step that can't take back its own
defaults fails here.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

from .const import ENTRY_DATA, MAC
from .original_entries import OPTIONS


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    return entry


async def _open(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert step in result["menu_options"]
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


def _suggested(result: dict[str, Any]) -> dict[str, Any]:
    """What the form shows filled in, as the frontend would send it back."""
    values = {}
    for field in result["data_schema"].schema:
        if (value := (field.description or {}).get("suggested_value")) is not None:
            values[str(field)] = value
        elif callable(getattr(field, "default", None)) and field.default() is not None:
            values[str(field)] = field.default()
    return values


async def _submit_suggested(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    assert result["type"] is FlowResultType.FORM, result
    values = _suggested(result)
    # The form's own schema accepts what it suggests.
    result["data_schema"](values)
    return await hass.config_entries.options.async_configure(result["flow_id"], values)


@pytest.mark.parametrize("step", ["image_config", "colors", "drawables", "sizes"])
async def test_each_step_takes_back_its_own_defaults(hass: HomeAssistant, step: str) -> None:
    entry = _entry(hass)

    result = await _submit_suggested(hass, await _open(hass, entry, step))

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_room_colors_keep_the_stored_colours(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await _submit_suggested(hass, await _open(hass, entry, "room_colors_step1"))
    assert result["step_id"] == "room_colors_step2"

    result = await _submit_suggested(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert list(entry.options["room_colors"]["16"][:3]) == [10, 20, 30]


async def test_room_id_that_is_no_number_shows_an_error_and_keeps_the_colours(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await _open(hass, entry, "room_colors_step1")

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"room_color_ids": ["kitchen"]})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room_colors_step1"
    assert result["errors"] == {"room_color_ids": "some_room_ids_are_not_integers"}
    assert entry.options["room_colors"] == OPTIONS["room_colors"]
