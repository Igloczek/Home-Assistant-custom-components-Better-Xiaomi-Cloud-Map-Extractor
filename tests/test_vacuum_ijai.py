"""Ijai vacuums: map download, WiFi serial number and map decryption.

The cloud answers with the responses recorded in tests/fixtures/ijai, the vacuum on the local
network with the recorded property answers, and the map is built and encrypted by
tests/ijai_map.py the way the vacuum uploads it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from miio.exceptions import DeviceException
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector import XiaomiCloudMapExtractorConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    FailedMapParseException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai import IjaiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import (
    XiaomiCloudConnector,
    XiaomiCloudDeviceInfo,
)
from custom_components.better_xiaomi_cloud_map_extractor.sensor import SENSOR_TYPES

from .const import TOKEN
from .ijai_map import (
    DEVICE_ID,
    LOGGED_IN_ID,
    MAC,
    OWNER_ID,
    ROOM,
    WIFI_SN,
    build_map,
    encrypt_map,
    fixture,
    map_key,
)

API = "https://de.api.io.mi.com/app"
STANDARD_URL = API + "/v2/home/get_interim_file_url"
PRO_URL = API + "/v2/home/get_interim_file_url_pro"
ACTION_URL = API + "/v2/miotspec/action"
MAP_URL = fixture("cloud_responses.json")["interim_file_url"]["result"]["url"]

CLOUD = fixture("cloud_responses.json")
LOCAL = fixture("local_properties.json")
UNREACHABLE = "unreachable"


class FakeCloud:
    """The Xiaomi cloud as the vacuum class sees it, answering with recorded responses."""

    def __init__(self, raw_map: bytes, urls: dict[str, Any], properties: Any, logged_in_id: str) -> None:
        self.connector = MagicMock(spec=XiaomiCloudConnector)
        self.connector.get_api_url = MagicMock(return_value=API)
        self.connector.execute_api_call_encrypted = AsyncMock(side_effect=self._call)
        self.connector.get_raw_map_data = AsyncMock(
            side_effect=lambda url: self.raw_map if url == MAP_URL else None
        )
        self.connector.get_other_info = AsyncMock(return_value=properties)
        self.connector.to_config = MagicMock(return_value=MagicMock(user_id=logged_in_id))
        self.raw_map = raw_map
        self.urls = urls
        self.calls: list[str] = []

    async def _call(self, url: str, params: dict[str, str]) -> Any:
        self.calls.append(url)
        if url == ACTION_URL:
            return {"code": 0, "message": "ok", "result": {"code": 0}}
        return self.urls[url]


@pytest.fixture
def ijai() -> Callable[..., tuple[IjaiCloudVacuum, FakeCloud]]:
    """Create an Ijai vacuum against the fake cloud and a vacuum answering on the local network."""
    patches = []

    def make(
        model: str,
        raw_map: bytes | None = None,
        *,
        local: str = "healthy",
        urls: dict[str, Any] | None = None,
        cloud_properties: Any = None,
        logged_in_id: str = OWNER_ID,
    ) -> tuple[IjaiCloudVacuum, FakeCloud]:
        cloud = FakeCloud(
            encrypt_map(model) if raw_map is None else raw_map,
            urls or {STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url"]},
            cloud_properties or CLOUD["get_properties_unknown"],
            logged_in_id,
        )
        miot_patch = patch(
            "custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai.MiotDevice"
        )
        miot = miot_patch.start().return_value
        patches.append(miot_patch)
        if local == UNREACHABLE:
            miot.get_property_by.side_effect = DeviceException("Unable to discover the device 192.0.2.10")
        else:
            miot.get_property_by.side_effect = lambda siid, piid: LOCAL[local][f"{siid}-{piid}"]
        device_info = XiaomiCloudDeviceInfo(
            device_id=DEVICE_ID,
            name="Vacuum",
            model=model,
            token=TOKEN,
            spec_type="",
            local_ip="192.0.2.10",
            mac=MAC,
            server="de",
            home_id=1,
            user_id=int(OWNER_ID),
        )
        config = VacuumConfig(
            cloud.connector, device_info, "de", DEVICE_ID, "192.0.2.10", TOKEN, model,
            ColorsPalette(), [], ImageConfig(), Sizes({}), [],
        )
        return IjaiCloudVacuum(config), cloud

    yield make
    for active in patches:
        active.stop()


# Map download --------------------------------------------------------------------------------


async def test_v1_downloads_the_map_from_the_standard_endpoint(ijai) -> None:
    """ijai.vacuum.v1 is not a K3 model: its map is on the standard endpoint (upstream PR #725, issue #742).

    The "pro" endpoint answers code -8 for it, and the integration asked only that one.
    """
    vacuum, cloud = ijai(
        "ijai.vacuum.v1",
        encrypt_map("ijai.vacuum.v1", wifi_sn="ABCDE12345FGHIJ6789"),
        local="v1",
        urls={STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert cloud.calls == [STANDARD_URL]


async def test_k3_model_falls_back_to_the_standard_endpoint(ijai) -> None:
    """A K3 model asks the "pro" endpoint first and the standard one when that fails (upstream PR #782)."""
    vacuum, cloud = ijai(
        "ijai.vacuum.v17",
        urls={STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert cloud.calls == [PRO_URL, STANDARD_URL]


async def test_non_k3_model_uses_the_standard_endpoint(ijai) -> None:
    """Models outside the Mi Home plugin's K3 list read the standard endpoint (upstream PR #725)."""
    vacuum, cloud = ijai(
        "ijai.vacuum.v10",
        urls={STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert cloud.calls == [STANDARD_URL]


async def test_k3_model_uses_the_pro_endpoint(ijai) -> None:
    vacuum, cloud = ijai("ijai.vacuum.v17")

    await vacuum.get_map()

    assert cloud.calls == [PRO_URL]


async def test_s12_requests_a_realtime_map_upload_through_the_cloud(ijai) -> None:
    """The S12 uploads a fresh map only when asked to; the request goes through the cloud (upstream PR #789)."""
    vacuum, cloud = ijai("xiaomi.vacuum.b106eu")

    await vacuum.get_map()

    assert cloud.calls[0] == ACTION_URL
    params = cloud.connector.execute_api_call_encrypted.await_args_list[0].args[1]
    assert params == {"data": f'{{"params": {{"did": "{DEVICE_ID}", "siid": 10, "aiid": 6, "in": [0]}}}}'}


async def test_failed_realtime_upload_request_still_downloads_the_map(ijai) -> None:
    vacuum, cloud = ijai("xiaomi.vacuum.b106eu")
    cloud.connector.execute_api_call_encrypted.side_effect = [FailedConnectionException(TimeoutError())] + [
        CLOUD["interim_file_url"]
    ] * 2

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None


async def test_other_models_do_not_request_an_upload(ijai) -> None:
    vacuum, cloud = ijai("ijai.vacuum.v3")

    await vacuum.get_map()

    assert ACTION_URL not in cloud.calls


# WiFi serial number --------------------------------------------------------------------------


async def test_v1_serial_with_19_characters(ijai) -> None:
    """ijai.vacuum.v1 reports a 19-character serial in property 7/45 only (issue #742)."""
    serial = "ABCDE12345FGHIJ6789"
    vacuum, _ = ijai("ijai.vacuum.v1", encrypt_map("ijai.vacuum.v1", wifi_sn=serial), local="v1")

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum._wifi_info_sn == serial


async def test_null_serial_property_is_skipped(ijai) -> None:
    """ijai.vacuum.v3 answers property 1/3 with `value: null` (upstream PR #787)."""
    vacuum, _ = ijai("ijai.vacuum.v3", local="v3_null")

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum._wifi_info_sn == WIFI_SN


@pytest.mark.parametrize("model", ["xiaomi.vacuum.d106gl", "ijai.vacuum.v17"])
async def test_serial_with_a_slash(ijai, model: str) -> None:
    """The S20 and S10 serial is `<5 digits>/<14 characters>` (issues #709 and #682).

    It was rejected, and the map key was built from None: "sequence item 0: expected str
    instance, NoneType found".
    """
    serial = "12345/A1BC2D3E456789"
    vacuum, _ = ijai(model, encrypt_map(model, wifi_sn=serial), local="s20")

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum._wifi_info_sn == serial


async def test_serial_from_the_cloud_when_the_vacuum_is_not_reachable(ijai) -> None:
    """Home Assistant cannot reach the vacuum on the local network (issues #698, #714; upstream PR #782)."""
    serial = "ABCDE12345FGHIJ6789"
    vacuum, cloud = ijai(
        "ijai.vacuum.v1",
        encrypt_map("ijai.vacuum.v1", wifi_sn=serial),
        local=UNREACHABLE,
        cloud_properties=CLOUD["get_properties_v1"],
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum._wifi_info_sn == serial
    cloud.connector.get_other_info.assert_awaited_once_with(
        DEVICE_ID,
        "get_properties",
        parameters=[
            {"did": DEVICE_ID, "siid": 1, "piid": 3},
            {"did": DEVICE_ID, "siid": 1, "piid": 5},
            {"did": DEVICE_ID, "siid": 7, "piid": 45},
        ],
    )


async def test_no_serial_anywhere_is_a_connection_error(ijai) -> None:
    """Without a serial the update fails with a connection error, not a TypeError from the key."""
    vacuum, _ = ijai("ijai.vacuum.v3", local=UNREACHABLE, cloud_properties=CLOUD["get_properties_unknown"])

    with pytest.raises(FailedConnectionException):
        await vacuum.get_map()


async def test_the_device_token_is_not_logged(ijai, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    vacuum, _ = ijai("ijai.vacuum.v3")

    await vacuum.get_map()

    assert TOKEN not in caplog.text


# Map key -------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["ijai.vacuum.v10", "xiaomi.vacuum.d106gl"])
async def test_models_without_a_hex_key(ijai, model: str) -> None:
    """Only some models use the MD5 digest itself as the key (upstream PR #725, "isEncryptKeyTypeHex")."""
    vacuum, _ = ijai(model)

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None


@pytest.mark.parametrize("model", ["ijai.vacuum.v3", "ijai.vacuum.v17", "ijai.vacuum.v18"])
@pytest.mark.parametrize("key_device_id", ["0", "2"])
async def test_map_encrypted_with_a_small_device_id(ijai, model: str, key_device_id: str) -> None:
    """These vacuums sometimes encrypt the map with "0" or "2" instead of the cloud device id.

    Issues #682 (v17), #696 (v18), #714 (v3); upstream PRs #785, #787, #792. It failed with
    "Padding is incorrect".
    """
    vacuum, _ = ijai(model, encrypt_map(model, device_id=key_device_id))

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum.additional_data()["enc_key"] == map_key(model, WIFI_SN, OWNER_ID, key_device_id).hex()


async def test_key_switches_between_maps(ijai) -> None:
    """ijai.vacuum.v18 encrypted one map with "0" and the next with its device id (issue #696)."""
    model = "ijai.vacuum.v18"
    vacuum, cloud = ijai(model, encrypt_map(model, device_id="0"))
    await vacuum.get_map()

    cloud.raw_map = encrypt_map(model)
    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum.additional_data()["enc_key"] == map_key(model, WIFI_SN, OWNER_ID, DEVICE_ID).hex()


async def test_shared_vacuum_map_encrypted_with_the_logged_in_account(ijai) -> None:
    """A vacuum shared from another account encrypts with the logged-in uid (issue #759, upstream PR #789)."""
    model = "xiaomi.vacuum.b106eu"
    vacuum, _ = ijai(model, encrypt_map(model, owner_id=LOGGED_IN_ID), logged_in_id=LOGGED_IN_ID)

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum.additional_data()["enc_key"] == map_key(model, WIFI_SN, LOGGED_IN_ID, DEVICE_ID).hex()


async def test_map_no_key_decrypts_is_a_parse_failure(ijai) -> None:
    """A map no known key decrypts raises FailedMapParseException, which keeps the last map."""
    model = "ijai.vacuum.v3"
    vacuum, _ = ijai(model, encrypt_map(model, wifi_sn="ZZZZZZZZZZZZZZZZZZ"))

    with pytest.raises(FailedMapParseException):
        await vacuum.get_map()


async def test_undecryptable_map_keeps_the_last_map(ijai) -> None:
    """The map extractor keeps serving the last map instead of failing the update (issue #682).

    "Padding is incorrect" escaped as an unexpected error, so setup did not finish and every
    entity turned unavailable while cleaning.
    """
    model = "ijai.vacuum.v3"
    vacuum, cloud = ijai(model)
    extractor = XiaomiCloudMapExtractorConnector(MagicMock(), MagicMock(), None)
    extractor._cloud_connector = MagicMock(is_authenticated=MagicMock(return_value=True))
    extractor._vacuum_connector = vacuum
    extractor._status = XiaomiCloudMapExtractorConnectorStatus.OK

    extractor.force_refresh()
    first = await extractor.get_data()
    last_map = first.map_data
    cloud.raw_map = encrypt_map(model, wifi_sn="ZZZZZZZZZZZZZZZZZZ")
    extractor.force_refresh()
    data = await extractor.get_data()

    assert data.status == XiaomiCloudMapExtractorConnectorStatus.FAILED_MAP_PARSING
    assert data.map_data is last_map


# Map parsing ---------------------------------------------------------------------------------


async def test_current_map_missing_from_the_map_list(ijai) -> None:
    """Maps uploaded while cleaning do not list the current map in mapInfo (issue #759, upstream PR #792).

    The parser failed with UnboundLocalError ('current_map').
    """
    model = "ijai.vacuum.v3"
    vacuum, _ = ijai(model, encrypt_map(model, robot_map=build_map(map_id=7, listed_maps=[1])))

    map_data, _ = await vacuum.get_map()

    assert map_data.rooms[ROOM + 1].name == "Kitchen"


async def test_cleaned_rooms_sensor_attributes(ijai) -> None:
    """The Ijai parser returns cleaned rooms as ids; the sensor attribute failed with AttributeError (upstream PR #786)."""
    vacuum, _ = ijai("ijai.vacuum.v3")
    map_data, _ = await vacuum.get_map()
    description = next(sensor for sensor in SENSOR_TYPES if sensor.key == "cleaned_rooms_ids")

    assert description.attributes_fn(map_data) == {"rooms_ids": [ROOM + 1]}
