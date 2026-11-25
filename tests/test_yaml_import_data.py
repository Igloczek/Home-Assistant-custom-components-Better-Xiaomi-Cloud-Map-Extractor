"""The v2 YAML camera turned into config entry data and options (`legacy.create_config_entry_data_from_yaml`).

The config flow tests replace this function; here it runs against the fake cloud, with the YAML
validated by the v2 schema like Home Assistant does before the import starts.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.const import CONF_PLATFORM
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.size import Size

from custom_components.better_xiaomi_cloud_map_extractor.const import (
    CONF_COLORS,
    CONF_IMAGE_CONFIG,
    CONF_SIZES,
    DOMAIN,
)
from custom_components.better_xiaomi_cloud_map_extractor.legacy import create_config_entry_data_from_yaml
from custom_components.better_xiaomi_cloud_map_extractor.legacy_schema import LEGACY_PLATFORM_SCHEMA

from .const import HOST, MAC, PASSWORD, TOKEN, USERNAME
from .fake_xiaomi import USER_ID, FakeXiaomiCloud

DEVICE = {
    "did": "123456789",
    "name": "Living room vacuum",
    "model": "viomi.vacuum.v8",
    "token": TOKEN,
    "spec_type": "urn:miot-spec-v2:device:vacuum:0000A006:viomi-v8:1",
    "localip": HOST,
    "mac": MAC.upper(),
}


def _yaml(**extra) -> dict:
    return LEGACY_PLATFORM_SCHEMA(
        {CONF_PLATFORM: DOMAIN, "host": HOST, "token": TOKEN, "username": USERNAME, "password": PASSWORD, **extra}
    )


def _cloud(devices: list[dict]) -> FakeXiaomiCloud:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("ok")
    cloud.sts_issuing("yaml-service-token")
    cloud.encrypted_api("/v2/homeroom/gethome", {"result": {"homelist": [{"id": "1", "uid": int(USER_ID)}]}})
    cloud.encrypted_api("/v2/home/home_device_list", {"result": {"device_info": devices}})
    return cloud


async def test_vacuum_found_in_the_cloud_fills_the_entry() -> None:
    yaml = _yaml(
        country="de",
        colors={"color_map_wall": [23, 23, 23]},
        map_transformation={"scale": 2, "rotate": 90, "trim": {"left": 5, "top": 10}},
        sizes={"vacuum_radius": 8},
    )

    data, options = await create_config_entry_data_from_yaml(yaml, _cloud([DEVICE]).session_creator)

    assert data == {
        "host": HOST,
        "token": TOKEN,
        "device_id": "123456789",
        "model": "viomi.vacuum.v8",
        "mac": MAC,
        "name": "Living room vacuum",
        "username": USERNAME,
        "password": PASSWORD,
        "server": "de",
        "used_map_api": None,
    }
    assert options[CONF_IMAGE_CONFIG] == {
        "scale": 2.0, "rotate": 90, "trim_left": 5.0, "trim_right": 0.0, "trim_top": 10.0, "trim_bottom": 0.0,
    }
    assert options[CONF_COLORS]["color_map_wall"] == (23, 23, 23, 255)
    assert len(options[CONF_COLORS]) == len(ColorsPalette.COLORS)
    assert options[CONF_SIZES][Size.VACUUM_RADIUS.value] == 8


async def test_vacuum_missing_from_the_cloud_still_gives_an_entry() -> None:
    """Setup asks to log in later and fills in the device; the YAML settings are kept."""
    data, _ = await create_config_entry_data_from_yaml(_yaml(country="de"), _cloud([]).session_creator)

    assert data["device_id"] is None
    assert data["mac"] is None
    assert data["server"] == "de"


async def test_failed_login_still_gives_an_entry(caplog: pytest.LogCaptureFixture) -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("invalid")

    with caplog.at_level(logging.ERROR):
        data, options = await create_config_entry_data_from_yaml(_yaml(), cloud.session_creator)

    assert data["host"] == HOST
    assert data["model"] is None
    assert options[CONF_IMAGE_CONFIG]["scale"] == 1
    assert "Failed to connect to Xiaomi Cloud" in caplog.text
