"""Diagnostics get attached to public issues: they must not carry secrets (upstream #778).

No account credentials, device token, host, MAC, Xiaomi session secret, map decryption key or
signed map URL. The map itself (parsed data and image) stays: that is what diagnostics are for.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from vacuum_map_parser_base.map_data import MapData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
    XiaomiCloudMapExtractorData,
)
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

from .const import ENTRY_DATA, HOST, MAC, PASSWORD, TOKEN, USERNAME
from .original_entries import OPTIONS, SESSION

ENC_KEY = "made-up-map-decryption-key-0f1e2d"
SIGNATURE = "made-up-url-signature-9a8b7c"
MAP_URL = f"https://cdn.example.com/map/obj?Expires=1&Signature={SIGNATURE}&KeyPairId=K1"
SECRETS = {
    "password": PASSWORD,
    "username": USERNAME,
    "device token": TOKEN,
    "host": HOST,
    "mac": MAC,
    "map key": ENC_KEY,
    "signed map url": SIGNATURE,
    **{name: value for name, value in SESSION["connector_config"].items() if name not in {"server", "user_id"}},
}


@pytest.mark.parametrize("vendor_data", [{"enc_key": ENC_KEY}, {"last_used_url": MAP_URL, "enc_key": ENC_KEY}])
async def test_diagnostics_carry_no_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_storage: dict[str, Any],
    map_connector: MagicMock,
    vendor_data: dict[str, str],
) -> None:
    key = f"{DOMAIN}_{MAC}"
    hass_storage[key] = {"version": 1, "key": key, "data": SESSION}
    map_connector.get_data.return_value = XiaomiCloudMapExtractorData(
        status=XiaomiCloudMapExtractorConnectorStatus.OK,
        map_data=MapData(),
        map_data_raw=b"raw map bytes",
        additional_vacuum_data=vendor_data,
    )
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dumped = json.dumps(diagnostics)
    leaked = {name for name, value in SECRETS.items() if value and str(value) in dumped}
    assert leaked == set()
    # Still useful for debugging: settings, status and which keys the vendor reported.
    assert diagnostics["config_entry_data"]["data"]["model"] == ENTRY_DATA["model"]
    assert diagnostics["config_entry_data"]["options"]["drawables"] == OPTIONS["drawables"]
    assert diagnostics["device_data"]["map_data"]["additional_vacuum_data"].keys() == vendor_data.keys()
    assert diagnostics["device_data"]["map_data_raw"] is not None
