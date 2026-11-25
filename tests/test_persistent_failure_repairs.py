"""Failures that keep happening become a Repairs issue with the most direct fix, gone once the map works.

The map update fails the same way several times in a row (FAILURE_THRESHOLD): the vacuum is not in
the configured Xiaomi region, the vacuum refuses the stored token, or Xiaomi has no map to download.
The fix flow looks the vacuum up in every region with the stored session, so nobody logs in again
or types a token from the log, and only asks for a login when the account really lacks the vacuum.
"""

from __future__ import annotations

import datetime
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from miio.exceptions import DeviceException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus as Status,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import XiaomiCloudMapExtractorData
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    DeviceNotFoundException,
    FailedConnectionException,
    InvalidDeviceTokenException,
)
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN
from custom_components.better_xiaomi_cloud_map_extractor.repairs import FAILURE_THRESHOLD
from custom_components.better_xiaomi_cloud_map_extractor.store import (
    restore_connector_config,
    save_connector_config,
)

from .fake_xiaomi import API, FakeResponse, FakeXiaomiCloud
from .test_actionable_failures import _other_regions_empty
from .test_session_lifecycle import DEVICE_ID, stored_session, vacuum_cloud

MAC = "60:1d:9d:3b:87:37"  # the vacuum vacuum_cloud() lists, on "de" at 192.168.1.14
CLOUD_HOST = "192.168.1.14"
CLOUD_TOKEN = "0" * 32
ENTRY = {
    "host": "192.168.1.99",
    "token": "f" * 32,
    "device_id": DEVICE_ID,
    "model": "viomi.vacuum.v8",
    "mac": MAC,
    "name": "Viomi V8",
    "username": "user@example.com",
    "password": "secret",
    "server": "us",
    "used_map_api": "VIOMI",
}


@pytest.fixture
def cloud() -> Generator[FakeXiaomiCloud]:
    cloud = vacuum_cloud()
    _other_regions_empty(cloud)
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.repairs.async_create_clientsession",
        lambda hass: cloud.session_creator(),
    ):
        yield cloud


async def _entry(hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock, **data) -> MockConfigEntry:
    flow = XiaomiCloudMapExtractorFlowHandler
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Viomi V8",
        data={**ENTRY, **data},
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
    session = stored_session(expires_in=datetime.timedelta(days=20))
    cloud.valid_service_tokens.add(session.service_token)
    await save_connector_config(hass, MAC, session)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await async_setup_component(hass, "repairs", {})
    return entry


def _fails(map_connector: MagicMock, failure: Exception | Status) -> None:
    if isinstance(failure, Status):
        map_connector.get_data.side_effect = None
        map_connector.get_data.return_value = XiaomiCloudMapExtractorData(status=failure)
    else:
        map_connector.get_data.side_effect = failure


async def _updates(entry: MockConfigEntry, count: int) -> None:
    for _ in range(count):
        await entry.runtime_data.coordinator.async_refresh()


def _issues(hass: HomeAssistant) -> dict[str, ir.IssueEntry]:
    return {
        issue.translation_key: issue
        for (domain, _), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    }


def _reauth_flows(hass: HomeAssistant) -> list:
    return [f for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN) if f["context"]["source"] == SOURCE_REAUTH]


FAILURES = {
    "not in the configured region": (DeviceNotFoundException(), "device_not_found"),
    "token refused (Roborock)": (InvalidDeviceTokenException(), "vacuum_token"),
    "token refused (MiOT)": (FailedConnectionException(DeviceException("token invalid")), "vacuum_token"),
    "map download keeps failing": (Status.FAILED_MAP_DOWNLOAD, "map_download_failing"),
}


@pytest.mark.parametrize(("failure", "key"), FAILURES.values(), ids=FAILURES.keys())
async def test_repeated_failure_raises_a_fixable_issue_and_success_clears_it(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock, failure, key: str
) -> None:
    entry = await _entry(hass, cloud, map_connector)

    _fails(map_connector, failure)
    await _updates(entry, FAILURE_THRESHOLD - 1)
    assert key not in _issues(hass), "a single bad update is not worth a repair"

    await _updates(entry, 1)
    issue = _issues(hass)[key]
    assert issue.is_fixable
    assert issue.translation_placeholders["name"] == "Viomi V8"
    assert not _reauth_flows(hass), "the vacuum, not the login, is the problem: no reauth"

    _fails(map_connector, Status.OK)
    await _updates(entry, 1)
    assert key not in _issues(hass)


async def test_failures_must_be_consecutive(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    for _ in range(3):
        _fails(map_connector, DeviceNotFoundException())
        await _updates(entry, FAILURE_THRESHOLD - 1)
        _fails(map_connector, Status.OK)
        await _updates(entry, 1)

    assert not _issues(hass)


async def _fix(hass: HomeAssistant, key: str) -> tuple:
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": _issues(hass)[key].issue_id})
    return manager, result


async def test_region_fix_moves_the_entry_to_the_region_that_has_the_vacuum(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "device_not_found")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_device"
    assert result["description_placeholders"]["server"] == "de"
    assert result["description_placeholders"]["host"] == CLOUD_HOST

    _fails(map_connector, Status.OK)
    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (entry.data["server"], entry.data["host"], entry.data["token"]) == ("de", CLOUD_HOST, CLOUD_TOKEN)
    assert entry.state is ConfigEntryState.LOADED
    assert not _issues(hass)


async def test_token_fix_offers_the_cloud_values_and_saves_what_is_submitted(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector, server="de")
    _fails(map_connector, InvalidDeviceTokenException())
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "vacuum_token")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "vacuum"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {"host": CLOUD_HOST, "token": CLOUD_TOKEN}

    _fails(map_connector, Status.OK)
    result = await manager.async_configure(result["flow_id"], {"host": "192.168.1.20", "token": CLOUD_TOKEN})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (entry.data["host"], entry.data["token"]) == ("192.168.1.20", CLOUD_TOKEN)
    assert not _issues(hass)


async def test_token_fix_without_the_cloud_offers_the_stored_values(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector, server="de")
    cloud.on("POST", f"{API}/v2/homeroom/gethome", lambda r: FakeResponse(status=502, body="<html/>"))
    _fails(map_connector, InvalidDeviceTokenException())
    await _updates(entry, FAILURE_THRESHOLD)

    _, result = await _fix(hass, "vacuum_token")

    assert result["step_id"] == "vacuum"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {"host": ENTRY["host"], "token": ENTRY["token"]}


async def test_map_download_fix_when_the_settings_already_match(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector, server="de", host=CLOUD_HOST, token=CLOUD_TOKEN)
    _fails(map_connector, Status.FAILED_MAP_DOWNLOAD)
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "map_download_failing")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings_match"

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["server"] == "de"


async def test_fix_when_the_account_does_not_have_the_vacuum_starts_reauth(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    cloud.encrypted_api("/v2/homeroom/gethome", {"result": {"homelist": []}})
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "device_not_found")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "not_in_account"

    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _reauth_flows(hass)


async def test_fix_renews_a_rejected_session_with_the_pass_token(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    cloud.valid_service_tokens.clear()  # the stored serviceToken expired
    cloud.pass_token_login()
    cloud.sts_issuing("renewed-service-token")
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)

    _, result = await _fix(hass, "device_not_found")

    assert result["step_id"] == "confirm_device"
    assert (await restore_connector_config(hass, MAC)).service_token == "renewed-service-token"


async def test_fix_with_a_session_that_cannot_be_renewed_starts_reauth(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    cloud.valid_service_tokens.clear()
    cloud.pass_token_login(accepted=False)
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "device_not_found")
    assert result["step_id"] == "login_needed"

    await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert _reauth_flows(hass)


async def test_fix_with_xiaomi_unreachable_shows_an_error_and_retries(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    cloud.on("POST", f"{API}/v2/homeroom/gethome", lambda r: FakeResponse(status=502, body="<html/>"))
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)

    manager, result = await _fix(hass, "device_not_found")
    assert result["step_id"] == "lookup_failed"
    assert result["errors"] == {"base": "cannot_connect"}

    cloud.encrypted_api(
        "/v2/homeroom/gethome", {"result": {"homelist": [{"id": "1", "uid": 1615523263}]}}
    )
    result = await manager.async_configure(result["flow_id"], {})
    assert result["step_id"] == "confirm_device"


async def test_removing_the_entry_removes_its_issues(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, map_connector: MagicMock
) -> None:
    entry = await _entry(hass, cloud, map_connector)
    _fails(map_connector, DeviceNotFoundException())
    await _updates(entry, FAILURE_THRESHOLD)
    assert _issues(hass)

    await hass.config_entries.async_remove(entry.entry_id)

    assert not _issues(hass)
