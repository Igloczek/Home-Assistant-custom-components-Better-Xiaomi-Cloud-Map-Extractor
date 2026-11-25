"""The "fit to the explored area" option of Ijai maps (upstream PR #794): stored, offered and passed on.

The rendering itself is tested through the real parser in tests/test_vacuum_ijai.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor import to_configuration
from custom_components.better_xiaomi_cloud_map_extractor.connector import XiaomiCloudMapExtractorConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai import IjaiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

from .conftest import make_device
from .const import ENTRY_DATA, MAC
from .original_entries import OPTIONS

IJAI_DATA = {**ENTRY_DATA, "model": "ijai.vacuum.v3", "used_map_api": "IJAI"}
# The image options v3.0.0-alpha-24 (688fe3e) writes: no fit option.
ALPHA_24_IMAGE_CONFIG = {
    "scale": 1.0, "rotate": 0, "trim_left": 0.0, "trim_right": 0.0, "trim_top": 0.0, "trim_bottom": 0.0,
}
ALPHA_24_OPTIONS = {**OPTIONS, "image_config": ALPHA_24_IMAGE_CONFIG}


def _entry(hass: HomeAssistant, data: dict[str, Any] = IJAI_DATA, **image_config: Any) -> MockConfigEntry:
    options = {**ALPHA_24_OPTIONS, "image_config": {**ALPHA_24_IMAGE_CONFIG, **image_config}}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=data, options=options)
    entry.add_to_hass(hass)
    return entry


async def test_entries_written_before_the_option_keep_the_full_map(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    assert to_configuration(entry).fit_to_explored is False


@pytest.mark.parametrize("fit", [True, False])
async def test_the_option_reaches_the_ijai_vacuum(hass: HomeAssistant, fit: bool) -> None:
    config = to_configuration(_entry(hass, fit_to_explored=fit))
    connector = XiaomiCloudMapExtractorConnector(MagicMock(), config, None)

    vacuum = connector._create_device(make_device(model="ijai.vacuum.v3"))

    assert isinstance(vacuum, IjaiCloudVacuum)
    assert vacuum._fit_to_explored is fit


async def _image_config_form(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "image_config"})
    assert result["type"] is FlowResultType.FORM
    return result


async def test_ijai_entry_offers_the_option_off(hass: HomeAssistant) -> None:
    result = await _image_config_form(hass, _entry(hass))

    fields = {str(field): field for field in result["data_schema"].schema}
    assert "fit_to_explored" in fields
    assert fields["fit_to_explored"].default() is False


async def test_ijai_entry_saves_the_option_with_the_other_image_settings(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await _image_config_form(hass, entry)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ALPHA_24_IMAGE_CONFIG, "scale": 2.0, "fit_to_explored": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["image_config"] == {**ALPHA_24_IMAGE_CONFIG, "scale": 2.0, "fit_to_explored": True}
    assert entry.options["colors"] == OPTIONS["colors"]


async def test_other_vacuums_do_not_offer_it(hass: HomeAssistant) -> None:
    """The fit only exists for Ijai maps; other vendors' forms stay as they were."""
    entry = _entry(hass, ENTRY_DATA)
    result = await _image_config_form(hass, entry)

    assert "fit_to_explored" not in {str(field) for field in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(result["flow_id"], ALPHA_24_IMAGE_CONFIG)
    assert entry.options["image_config"] == ALPHA_24_IMAGE_CONFIG
