"""Regression tests for Home Assistant architecture, resource leaks, and security hygiene."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.better_xiaomi_cloud_map_extractor.camera import XiaomiCloudMapExtractorCamera
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
    XiaomiCloudMapExtractorData,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import DeviceNotFoundException
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.utils import generate_enc_params
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN
from custom_components.better_xiaomi_cloud_map_extractor.image import XiaomiCloudMapExtractorImageEntity
from custom_components.better_xiaomi_cloud_map_extractor.issues import async_report_update
from custom_components.better_xiaomi_cloud_map_extractor.switch import (
    SWITCH_TYPES,
    XiaomiCloudMapExtractorSwitchEntity,
)

from .const import ENTRY_DATA, MAC, PASSWORD, USERNAME
from .fake_xiaomi import FakeResponse, FakeXiaomiCloud, QR_LINK, SSECURITY
from .original_entries import OPTIONS
from .test_cloud_session import stored_config


async def test_cloud_http_responses_are_always_released() -> None:
    """Every aiohttp response (including non-200 downloads and header/cookie-only hops) is released."""
    cloud = FakeXiaomiCloud()
    cloud.sts_issuing("fresh-service-token")
    cloud.password_login()
    cloud.pass_token_login()
    cloud.on("GET", "https://cdn.example.com/map/missing", lambda _: FakeResponse(status=404, body=b"missing"))

    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    assert await connector.login_with_credentials(USERNAME, PASSWORD)
    assert await connector.get_raw_map_data("https://cdn.example.com/map/missing") is None

    stored = await XiaomiCloudConnector.from_config(stored_config(), cloud.session_creator)
    assert await stored.renew_session()

    qr_cloud = FakeXiaomiCloud()
    qr_cloud.qr_login("qr-service-token")
    qr_cloud.on(
        "GET",
        QR_LINK,
        lambda _: FakeResponse(
            status=302, headers={"Location": "https://account.xiaomi.com/pass/serviceLogin?_json=true&callback=ok"}
        ),
    )
    qr_connector = XiaomiCloudConnector(qr_cloud.session_creator, server="de")
    await qr_connector.login_with_qr_get_code()
    await qr_connector.login_with_qr_wait_for_completion()

    unreleased = [str(r.url) for r in (*cloud.responses, *qr_cloud.responses) if not r.released]
    assert unreleased == []


async def test_diagnostics_handles_missing_coordinator_data(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    map_connector: MagicMock,
) -> None:
    """Downloading diagnostics before data is populated never crashes with AttributeError."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entry.runtime_data.coordinator.data = None
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["device_data"] is None
    assert diagnostics["config_entry_data"]["data"]["model"] == ENTRY_DATA["model"]


async def test_entities_do_not_retain_plaintext_credentials(
    hass: HomeAssistant,
    map_connector: MagicMock,
) -> None:
    """Entity instances should not hold unused plaintext account passwords, usernames, or device tokens."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    image_entity = XiaomiCloudMapExtractorImageEntity(hass, coordinator, entry)
    camera_entity = XiaomiCloudMapExtractorCamera(coordinator, entry)

    for entity in (image_entity, camera_entity):
        for attr in ("_password", "_username", "_token"):
            assert not hasattr(entity, attr), f"{type(entity).__name__} should not store {attr}"


async def test_switch_accepts_service_kwargs(
    hass: HomeAssistant,
    map_connector: MagicMock,
) -> None:
    """SwitchEntity overrides must accept **kwargs per Home Assistant's ToggleEntity signature."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    entity = XiaomiCloudMapExtractorSwitchEntity(coordinator, entry, SWITCH_TYPES[0])
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_off(unused_kwarg=True)
    await entity.async_turn_on(unused_kwarg=True)


def test_generate_enc_params_does_not_mutate_caller_dict() -> None:
    """Encrypting request parameters must not mutate the caller's dictionary in place."""
    original = {"data": '{"method":"get_prop","params":[]}'}
    snapshot = dict(original)

    encrypted = generate_enc_params(
        "https://de.api.io.mi.com/app/v2/home/rpc/123",
        "POST",
        SSECURITY,
        "nonce123",
        original,
        SSECURITY,
    )

    assert original == snapshot
    assert "signature" in encrypted
    assert "signature" not in original


async def test_removing_entry_clears_failure_counters(
    hass: HomeAssistant,
    map_connector: MagicMock,
) -> None:
    """Deleting a config entry leaves no stale failure counters in hass.data."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async_report_update(hass, entry, DeviceNotFoundException())
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    for value in hass.data.values():
        if isinstance(value, dict):
            assert entry.entry_id not in value
            for nested in value.values():
                if isinstance(nested, dict):
                    assert entry.entry_id not in nested


async def test_camera_and_image_return_bytes_without_executor_job(
    hass: HomeAssistant,
    map_connector: MagicMock,
) -> None:
    """Returning already-cached map image bytes should not dispatch executor threads."""
    map_connector.get_data.return_value = XiaomiCloudMapExtractorData(
        status=XiaomiCloudMapExtractorConnectorStatus.OK,
        map_image=b"\x89PNG\r\n",
    )
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    camera = XiaomiCloudMapExtractorCamera(coordinator, entry)
    camera.hass = hass
    image = XiaomiCloudMapExtractorImageEntity(hass, coordinator, entry)

    with patch.object(hass, "async_add_executor_job", side_effect=AssertionError("unexpected executor hop")):
        assert await camera.async_camera_image() == b"\x89PNG\r\n"
        assert await image.async_image() == b"\x89PNG\r\n"
