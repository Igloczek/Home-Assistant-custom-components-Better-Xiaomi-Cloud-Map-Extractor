"""A cloud session imported from the original integration keeps working without a password login."""

from __future__ import annotations

import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
)
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN, ORIGINAL_DOMAIN
from custom_components.better_xiaomi_cloud_map_extractor.migration import async_copy_original_session
from custom_components.better_xiaomi_cloud_map_extractor.store import restore_connector_config, save_connector_config

from .const import MAC
from .fake_xiaomi import PASS_TOKEN, SERVICE_LOGIN, SSECURITY, USER_ID
from .test_session_lifecycle import make_connector, no_map_parsing, vacuum_cloud  # noqa: F401


def original_store(hass_storage: dict[str, Any], *, expiration: str, pass_token: str | None) -> None:
    """The original's `.storage/xiaomi_cloud_map_extractor_<mac>`; values are made up."""
    config = {
        "username": "user@example.com",
        "password": "made-up-password",
        "server": "de",
        "user_id": USER_ID,
        "c_user_id": "cUser-abc",
        "service_token": "stored-service-token",
        "expiration": expiration,
        "ssecurity": SSECURITY,
        "device_id": "abcdef",
    }
    if pass_token is not None:  # only builds with upstream PR #777 store it
        config["pass_token"] = pass_token
    key = f"{ORIGINAL_DOMAIN}_{MAC}"
    hass_storage[key] = {"version": 1, "key": key, "data": {"connector_config": config}}


async def imported_session(hass: HomeAssistant):
    await async_copy_original_session(hass, MAC)
    return await restore_connector_config(hass, MAC)


class StoreSaver:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def __call__(self, config) -> None:
        await save_connector_config(self.hass, MAC, config)


async def test_imported_pass_token_renews_without_password(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    expiring = (datetime.datetime.now() + datetime.timedelta(hours=6)).isoformat()
    original_store(hass_storage, expiration=expiring, pass_token=PASS_TOKEN)
    session = await imported_session(hass)
    assert session.pass_token == PASS_TOKEN

    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.pass_token_login()
    cloud.password_login("captcha")
    cloud.sts_issuing("renewed-service-token")
    data = await make_connector(cloud, session, StoreSaver(hass)).get_data()

    assert data.status == XiaomiCloudMapExtractorConnectorStatus.OK
    assert cloud.password_login_count() == 0
    stored = hass_storage[f"{DOMAIN}_{MAC}"]["data"]["connector_config"]
    assert stored["service_token"] == "renewed-service-token"
    assert stored["pass_token"] == PASS_TOKEN


async def test_imported_alpha24_session_is_used_until_xiaomi_rejects_it(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """alpha-24 stores have no passToken; an expiring but valid session must not trigger a password login."""
    expiring_utc = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=6)).isoformat()
    original_store(hass_storage, expiration=expiring_utc, pass_token=None)
    session = await imported_session(hass)
    assert session.pass_token is None

    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.password_login("captcha")
    data = await make_connector(cloud, session, StoreSaver(hass)).get_data()

    assert data.status == XiaomiCloudMapExtractorConnectorStatus.OK
    assert cloud.calls(SERVICE_LOGIN) == []
    assert cloud.password_login_count() == 0
