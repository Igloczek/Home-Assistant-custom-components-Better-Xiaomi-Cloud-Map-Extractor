"""Ijai vacuums: map download, WiFi serial number and map decryption.

The cloud answers with the responses recorded in tests/fixtures/ijai, the vacuum on the local
network with the recorded property answers, and the map is built and encrypted by
tests/ijai_map.py the way the vacuum uploads it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from miio.exceptions import DeviceException
from PIL import Image, ImageChops
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig, TrimConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_base.map_data import Point

from custom_components.better_xiaomi_cloud_map_extractor.connector import XiaomiCloudMapExtractorConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    FailedMapDownloadException,
    FailedMapParseException,
    MapSavingDisabledException,
    SessionExpiredException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums import vacuum_ijai
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai import IjaiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import (
    XiaomiCloudConnector,
    XiaomiCloudDeviceInfo,
)
from custom_components.better_xiaomi_cloud_map_extractor.sensor import SENSOR_TYPES

from .const import TOKEN
from .log_secrets import assert_no_secrets
from .ijai_map import (
    CANVAS,
    DEVICE_ID,
    LOGGED_IN_ID,
    MAC,
    OWNER_ID,
    ROOM,
    WIFI_SN,
    build_island_map,
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
    """The Xiaomi cloud as the vacuum class sees it, answering with recorded responses.

    `get_properties` answers only the properties asked for, from the entries of the recorded
    responses; any other property gets code -4003, as from the real cloud. (It used to hand back
    one recorded answer whatever was asked, which hid a second request for another property.)
    """

    def __init__(self, raw_map: bytes, urls: dict[str, Any], properties: list[Any], logged_in_id: str) -> None:
        self.connector = MagicMock(spec=XiaomiCloudConnector)
        self.connector.get_api_url = MagicMock(return_value=API)
        self.connector.execute_api_call_encrypted = AsyncMock(side_effect=self._call)
        self.connector.get_raw_map_data = AsyncMock(
            side_effect=lambda url: self.raw_map if url == MAP_URL else None
        )
        self.properties = {
            (entry["siid"], entry["piid"]): entry for response in properties for entry in response["result"]
        }
        self.connector.get_other_info = AsyncMock(side_effect=self._get_properties)
        self.connector.to_config = MagicMock(return_value=MagicMock(user_id=logged_in_id))
        self.raw_map = raw_map
        self.urls = urls
        self.calls: list[str] = []

    async def _get_properties(self, device_id: str, method: str, parameters: list[dict[str, Any]]) -> Any:
        assert method == "get_properties"
        return {"code": 0, "message": "ok", "result": [
            self.properties.get((p["siid"], p["piid"]), {**p, "code": -4003}) for p in parameters
        ]}

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
        local: str | dict[str, Any] = "healthy",
        urls: dict[str, Any] | None = None,
        cloud_properties: Any = None,
        logged_in_id: str = OWNER_ID,
        **render: Any,
    ) -> tuple[IjaiCloudVacuum, FakeCloud]:
        cloud = FakeCloud(
            encrypt_map(model) if raw_map is None else raw_map,
            urls or {STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url"]},
            cloud_properties if isinstance(cloud_properties, list) else [cloud_properties or CLOUD["get_properties_unknown"]],
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
            answers = LOCAL[local] if isinstance(local, str) else local
            miot.get_property_by.side_effect = lambda siid, piid: answers[f"{siid}-{piid}"]
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
            render.pop("palette", ColorsPalette()), render.pop("drawables", []),
            render.pop("image_config", ImageConfig()), render.pop("sizes", Sizes({})), [], **render,
        )
        return IjaiCloudVacuum(config), cloud

    yield make
    for active in patches:
        active.stop()


# Vacuums that worked on v3.0.0-alpha-24 -----------------------------------------------------
#
# v3.0.0-alpha-24 (688fe3e, vacuum-map-parser-ijai 0.1.0) asked only the "pro" endpoint, read the
# serial from properties 1/3, 1/5 and 7/45 in that order with stricter rules, and used the full MD5
# digest of serial + home owner uid + cloud device id as the key for every model. A vacuum that
# worked there must make the same requests and decrypt with the same key. These tests also pass
# on the alpha-24 vacuum code with parser 0.1.0.

IJAI_MODELS = [
    *(f"ijai.vacuum.v{n}" for n in (1, 2, 3, 10, 13, 14, 15, 16, 17, 18, 19)),
    "xiaomi.vacuum.b106eu",
    "xiaomi.vacuum.c103",
    "xiaomi.vacuum.d106gl",
]


def _properties(p13: Any = None, p15: Any = None, p745: Any = None) -> dict[str, Any]:
    def answer(siid: int, piid: int, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return [{"did": f"{siid}-{piid}", "siid": siid, "piid": piid, "code": -4003}]
        return [{"did": f"{siid}-{piid}", "siid": siid, "piid": piid, "code": 0, "value": value}]

    return {"1-3": answer(1, 3, p13), "1-5": answer(1, 5, p15), "7-45": answer(7, 45, p745)}


@pytest.mark.parametrize("model", IJAI_MODELS)
async def test_alpha_24_vacuum_makes_the_same_requests_and_key(ijai, model: str) -> None:
    vacuum, cloud = ijai(model, encrypt_map(model, hex_key=True))

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert [url for url in cloud.calls if url != ACTION_URL] == [PRO_URL]
    cloud.connector.get_raw_map_data.assert_awaited_once_with(MAP_URL)
    assert vacuum._miot_device.get_property_by.call_args_list == [call(1, 3)]
    cloud.connector.get_other_info.assert_not_awaited()
    assert vacuum.additional_data()["enc_key"] == map_key(model, WIFI_SN, OWNER_ID, DEVICE_ID, hex_key=True).hex()


OTHER_SN = "ZYXWVU987654TSRQPO"


@pytest.mark.parametrize(
    ("properties", "serial", "reads"),
    [
        pytest.param(_properties("ABCDEF123456GHIJKL"), WIFI_SN, [(1, 3)], id="1/3"),
        pytest.param(_properties("", WIFI_SN), WIFI_SN, [(1, 3), (1, 5)], id="1/5"),
        pytest.param(
            _properties("ABCDE12345FGHIJ6789", WIFI_SN), WIFI_SN, [(1, 3), (1, 5)], id="1/5 over a 19-character 1/3"
        ),
        pytest.param(
            _properties("", "", f'["1","{OTHER_SN}","{WIFI_SN}","0"]'),
            WIFI_SN, [(1, 3), (1, 5), (7, 45)], id="7/45 last match",
        ),
        pytest.param(
            _properties("", "", f'["1","{WIFI_SN};{OWNER_ID}","{OTHER_SN}","0"]'),
            OTHER_SN, [(1, 3), (1, 5), (7, 45)], id="7/45 last match after the owner entry",
        ),
        pytest.param(
            _properties("", "", f'["1","{WIFI_SN}","12345/A1BC2D3E456789","ABCDE12345FGHIJ6789"]'),
            WIFI_SN, [(1, 3), (1, 5), (7, 45)], id="7/45 over a slash and a 19-character serial",
        ),
    ],
)
async def test_alpha_24_serial_is_still_chosen(
    ijai, properties: dict[str, Any], serial: str, reads: list[tuple[int, int]]
) -> None:
    model = "ijai.vacuum.v3"
    vacuum, cloud = ijai(model, encrypt_map(model, wifi_sn=serial, hex_key=True), local=properties)

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert vacuum._wifi_info_sn == serial
    assert vacuum._miot_device.get_property_by.call_args_list == [call(*read) for read in reads]
    cloud.connector.get_other_info.assert_not_awaited()


@pytest.mark.parametrize("model", IJAI_MODELS)
def test_alpha_24_key_is_tried_first(ijai, model: str) -> None:
    vacuum, _ = ijai(model)

    assert vacuum._map_key_id_candidates()[0] == (OWNER_ID, DEVICE_ID, True)


@pytest.mark.parametrize("model", IJAI_MODELS)
def test_key_derivation_matches_the_parser(model: str) -> None:
    """The integration derives keys itself to try both key types; it must agree with the parser's."""
    # Imported here: the tests above also run against the alpha-24 code and parser 0.1.0, which lack these.
    from vacuum_map_parser_ijai.aes_decryptor import gen_md5_key
    from vacuum_map_parser_ijai.status_mapping import is_EncryptKeyTypeHex_model

    from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai import (
        map_key as map_key_from_integration,
    )

    assert map_key_from_integration(WIFI_SN, OWNER_ID, DEVICE_ID, model, MAC, is_EncryptKeyTypeHex_model(model)) == (
        gen_md5_key(WIFI_SN, OWNER_ID, DEVICE_ID, model, MAC)
    )


@pytest.mark.parametrize("model", ["ijai.vacuum.v3", "ijai.vacuum.v10", "xiaomi.vacuum.d106gl"])
async def test_side_by_side_with_parser_0_1_0_decrypts_maps(
    ijai, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """Side-by-side install with the original integration can keep vacuum-map-parser-ijai 0.1.0 loaded.

    In 0.1.0, aes_decrypt takes 2 positional arguments and hardcodes hex keys (issue #9).
    Map decryption must work for both hex-key and short-key models without calling it.
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    from vacuum_map_parser_ijai import aes_decryptor

    def aes_decrypt_0_1_0(data: bytes, key: str) -> bytes:
        cipher = AES.new(bytes.fromhex(key), AES.MODE_ECB)
        return bytes.fromhex(unpad(cipher.decrypt(data), AES.block_size, "pkcs7").decode("utf-8"))

    monkeypatch.setattr(aes_decryptor, "aes_decrypt", aes_decrypt_0_1_0)
    if hasattr(vacuum_ijai, "aes_decrypt"):
        monkeypatch.setattr(vacuum_ijai, "aes_decrypt", aes_decrypt_0_1_0)

    vacuum, _ = ijai(model)

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None


@pytest.mark.parametrize(
    ("model", "hex_key"),
    [("ijai.vacuum.v3", True), ("ijai.vacuum.v10", False), ("xiaomi.vacuum.d106gl", False)],
)
def test_aes_helpers_match_parser_0_1_1(model: str, hex_key: bool) -> None:
    """Local AES-ECB helpers must match vacuum-map-parser-ijai 0.1.1 for both key types."""
    import base64
    import zlib
    from vacuum_map_parser_ijai.aes_decryptor import (
        aes_decrypt as parser_aes_decrypt,
        aes_encrypt as parser_aes_encrypt,
    )

    key = vacuum_ijai.map_key(WIFI_SN, OWNER_ID, DEVICE_ID, model, MAC, hex_key)
    raw_map = encrypt_map(model, hex_key=hex_key)

    expected_compressed = parser_aes_decrypt(raw_map, key, hex_key)
    assert vacuum_ijai._aes_decrypt(raw_map, key, hex_key) == expected_compressed
    assert vacuum_ijai.unpack_map(raw_map, key, hex_key) == zlib.decompress(expected_compressed)
    assert vacuum_ijai.unpack_map(base64.b64encode(raw_map), key, hex_key) == zlib.decompress(expected_compressed)

    sample_plain = "+".join([WIFI_SN, OWNER_ID, DEVICE_ID])
    model_suffix = model.split(".")[-1][-4:].rjust(4, "0")
    sample_key = MAC.lower().replace(":", "") + model_suffix
    assert vacuum_ijai._aes_encrypt(sample_plain, sample_key) == parser_aes_encrypt(sample_plain, sample_key)


# Map download --------------------------------------------------------------------------------


async def test_v1_falls_back_to_the_standard_endpoint(ijai) -> None:
    """ijai.vacuum.v1 keeps its map on the standard endpoint (upstream PRs #725 and #782, issue #742).

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
    assert cloud.calls == [PRO_URL, STANDARD_URL]


async def test_k3_model_falls_back_to_the_standard_endpoint(ijai) -> None:
    """A K3 model asks the "pro" endpoint first and the standard one when that fails (upstream PR #782)."""
    vacuum, cloud = ijai(
        "ijai.vacuum.v17",
        urls={STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert cloud.calls == [PRO_URL, STANDARD_URL]


async def test_no_map_on_either_endpoint_is_a_download_failure(ijai) -> None:
    vacuum, cloud = ijai(
        "ijai.vacuum.v3",
        urls={STANDARD_URL: CLOUD["interim_file_url_pro_no_storage"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    with pytest.raises(FailedMapDownloadException):
        await vacuum.get_map()


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


async def test_expired_session_on_the_upload_request_is_not_swallowed(ijai) -> None:
    """The extractor renews the session when Xiaomi rejects it; the upload request must let that through."""
    vacuum, cloud = ijai("xiaomi.vacuum.b106eu")
    cloud.connector.execute_api_call_encrypted.side_effect = SessionExpiredException()

    with pytest.raises(SessionExpiredException):
        await vacuum.get_map()


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


async def test_expired_session_on_the_cloud_serial_is_not_hidden(ijai) -> None:
    """A rejected session while reading the serial through the cloud is renewed, not reported as a connection error."""
    vacuum, cloud = ijai("ijai.vacuum.v3", local=UNREACHABLE)
    cloud.connector.get_other_info.side_effect = SessionExpiredException()

    with pytest.raises(SessionExpiredException):
        await vacuum.get_map()


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
    extractor._cloud.cloud_connector = MagicMock(spec=XiaomiCloudConnector)
    extractor._cloud.cloud_connector.has_service_token.return_value = True
    extractor._cloud.cloud_connector.is_session_expiring.return_value = False
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


# Map saving turned off in Xiaomi Home -----------------------------------------------------------
#
# With map saving off in the Xiaomi Home app, an Ijai map fails to decrypt ("Padding is incorrect")
# or the cloud has none (upstream #759: turning it back on fixed the map). The switch is
# remember-state, siid 10 piid 1 (0 Close, 1 Open) in the miot-spec of every ijai.vacuum model and of
# xiaomi.vacuum.b106eu, c103 and d106gl. It is read through the cloud, only once the map failed.

MAP_SAVING_OFF = CLOUD["get_properties_map_saving_off"]
MAP_SAVING_ON = CLOUD["get_properties_map_saving_on"]
MAP_SAVING_PROPERTY = [{"did": DEVICE_ID, "siid": 10, "piid": 1}]
WRONG_SERIAL = "ZZZZZZZZZZZZZZZZZZ"


@pytest.mark.parametrize("model", ["xiaomi.vacuum.b106eu", "ijai.vacuum.v3", "ijai.vacuum.v17"])
async def test_map_saving_off_explains_an_undecryptable_map(ijai, model: str) -> None:
    vacuum, cloud = ijai(model, encrypt_map(model, wifi_sn=WRONG_SERIAL), cloud_properties=[MAP_SAVING_OFF])

    with pytest.raises(MapSavingDisabledException) as failure:
        await vacuum.get_map()

    assert "Turn on map saving in the Xiaomi Home app" in str(failure.value)
    cloud.connector.get_other_info.assert_awaited_once_with(DEVICE_ID, "get_properties", parameters=MAP_SAVING_PROPERTY)


async def test_map_saving_off_explains_a_missing_map(ijai) -> None:
    no_map = CLOUD["interim_file_url_pro_no_storage"]
    vacuum, _ = ijai(
        "ijai.vacuum.v3", urls={STANDARD_URL: no_map, PRO_URL: no_map}, cloud_properties=[MAP_SAVING_OFF]
    )

    with pytest.raises(MapSavingDisabledException):
        await vacuum.get_map()


@pytest.mark.parametrize(
    "answer", [MAP_SAVING_ON, CLOUD["get_properties_unknown"]], ids=["map saving on", "switch not reported"]
)
async def test_map_saving_on_keeps_the_parse_failure(ijai, answer: Any) -> None:
    """Only a vacuum that reports map saving off gets the message; any other failure stays as it was."""
    model = "ijai.vacuum.v3"
    vacuum, _ = ijai(model, encrypt_map(model, wifi_sn=WRONG_SERIAL), cloud_properties=[answer])

    with pytest.raises(FailedMapParseException):
        await vacuum.get_map()


async def test_unreadable_map_saving_switch_keeps_the_parse_failure(ijai) -> None:
    model = "ijai.vacuum.v3"
    vacuum, cloud = ijai(model, encrypt_map(model, wifi_sn=WRONG_SERIAL))
    cloud.connector.get_other_info.side_effect = FailedConnectionException(TimeoutError())

    with pytest.raises(FailedMapParseException):
        await vacuum.get_map()


async def test_a_working_map_does_not_ask_about_map_saving(ijai) -> None:
    vacuum, cloud = ijai("ijai.vacuum.v3", cloud_properties=[MAP_SAVING_OFF])

    await vacuum.get_map()

    cloud.connector.get_other_info.assert_not_awaited()


def _extractor(vacuum: IjaiCloudVacuum) -> XiaomiCloudMapExtractorConnector:
    extractor = XiaomiCloudMapExtractorConnector(MagicMock(), MagicMock(), None)
    extractor._cloud.cloud_connector = MagicMock(spec=XiaomiCloudConnector)
    extractor._cloud.cloud_connector.has_service_token.return_value = True
    extractor._cloud.cloud_connector.is_session_expiring.return_value = False
    extractor._vacuum_connector = vacuum
    extractor._status = XiaomiCloudMapExtractorConnectorStatus.OK
    return extractor


async def test_map_saving_off_keeps_the_last_map(ijai, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    model = "xiaomi.vacuum.b106eu"
    vacuum, cloud = ijai(model, cloud_properties=[MAP_SAVING_OFF])
    extractor = _extractor(vacuum)
    extractor.force_refresh()
    last_map = (await extractor.get_data()).map_data

    cloud.raw_map = encrypt_map(model, wifi_sn=WRONG_SERIAL)
    extractor.force_refresh()
    data = await extractor.get_data()

    assert data.status == XiaomiCloudMapExtractorConnectorStatus.MAP_SAVING_DISABLED
    assert data.map_data is last_map
    assert "Turn on map saving in the Xiaomi Home app" in caplog.text
    assert_no_secrets(caplog)


async def test_map_saving_off_without_a_map_fails_the_update(ijai, caplog: pytest.LogCaptureFixture) -> None:
    """With no map to keep, the update fails, so Home Assistant shows the message (setup: "will retry")."""
    caplog.set_level(logging.DEBUG)
    model = "xiaomi.vacuum.b106eu"
    vacuum, _ = ijai(model, encrypt_map(model, wifi_sn=WRONG_SERIAL), cloud_properties=[MAP_SAVING_OFF])
    extractor = _extractor(vacuum)

    with pytest.raises(MapSavingDisabledException) as failure:
        await extractor.get_data()

    assert extractor._map_cache.status == XiaomiCloudMapExtractorConnectorStatus.MAP_SAVING_DISABLED
    assert_no_secrets(caplog, None, str(failure.value))


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


# Fit to the explored area (upstream PR #794) ------------------------------------------------
#
# Ijai vacuums upload a fixed canvas (800x800) however little of it is explored, so the floor plan
# is a small island in a large background. With "fit to the explored area" on, the map is cropped
# to the explored pixels plus a margin and zoomed to about 960 px on its longer edge, and the icons
# and room names zoom with it. Off (the default, and every entry written before the option) the
# image is exactly what it was. The maps go through the real parser and renderer.

CHARGER = (0, 255, 0, 255)
ROOM_NAME = (255, 0, 255)
PALETTE = ColorsPalette(
    {
        SupportedColor.CHARGER: CHARGER,
        SupportedColor.CHARGER_OUTLINE: CHARGER,
        SupportedColor.ROOM_NAMES: ROOM_NAME,
    },
    {str(ROOM + 1): (128, 128, 128)},
)
DRAWN = [Drawable.CHARGER, Drawable.ROOM_NAMES]
# ISLAND plus the 15-pixel margin: 70 x 50 source pixels, zoomed to 960 px on the longer edge.
FIT_SCALE = 960 / 70


def _box(image: Image.Image, color: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Bounding box of the pixels whose strongest channels are those of `color` (text is antialiased)."""
    bands = image.convert("RGBA").split()[:3]
    strong = [bands[i] for i in range(3) if color[i] == 255]
    weak = [bands[i] for i in range(3) if color[i] == 0]
    mask = Image.new("L", image.size, 255)
    for high in strong:
        for low in weak:
            mask = ImageChops.multiply(mask, ImageChops.subtract(high, low).point(lambda v: 255 if v > 60 else 0))
    box = mask.getbbox()
    assert box is not None, f"nothing drawn in {color}"
    return box


async def _render(ijai, robot_map: bytes | None = None, **render: Any):
    model = "ijai.vacuum.v3"
    render = {"palette": PALETTE, "drawables": DRAWN, **render}
    vacuum, _ = ijai(model, encrypt_map(model, robot_map=build_island_map() if robot_map is None else robot_map), **render)
    map_data, _ = await vacuum.get_map()
    return map_data


async def test_fit_is_off_by_default(ijai) -> None:
    """Without the option the whole canvas is drawn, as on v3.0.0-alpha-24."""
    map_data = await _render(ijai)

    assert map_data.image.data.size == (CANVAS, CANVAS)


async def test_fit_crops_to_the_explored_area_and_zooms_it(ijai) -> None:
    map_data = await _render(ijai, fit_to_explored=True)

    assert map_data.image.data.size == (int(70 * FIT_SCALE), int(50 * FIT_SCALE))
    assert map_data.rooms[ROOM + 1].name == "Kitchen"


async def test_fit_keeps_coordinates_on_the_drawn_map(ijai) -> None:
    """A map point lands on the same spot of the floor plan: shifted by the crop, times the zoom."""
    full = await _render(ijai)
    fitted = await _render(ijai, fit_to_explored=True)
    room = fitted.rooms[ROOM + 1]
    corner = Point(room.x0, room.y0)

    on_full = corner.to_img(full.image.dimensions)
    on_fitted = corner.to_img(fitted.image.dimensions)
    # The crop removes 45 columns on the left and 65 rows at the top (ISLAND minus the margin).
    assert on_fitted.x == pytest.approx((on_full.x - 45) * FIT_SCALE)
    assert on_fitted.y == pytest.approx((on_full.y - 65) * FIT_SCALE)


async def test_fit_zooms_icons_and_room_names_with_the_map(ijai) -> None:
    """The charger and the room name keep their size relative to the map, instead of shrinking."""
    full = await _render(ijai)
    fitted = await _render(ijai, fit_to_explored=True)

    for color in (CHARGER, ROOM_NAME):
        left, top, right, bottom = _box(full.image.data, color)
        f_left, f_top, f_right, f_bottom = _box(fitted.image.data, color)
        assert (f_right - f_left) / (right - left) == pytest.approx(FIT_SCALE, rel=0.2), color
        assert (f_bottom - f_top) / (bottom - top) == pytest.approx(FIT_SCALE, rel=0.25), color


async def test_fit_never_loosens_the_configured_trim_and_scale(ijai) -> None:
    """A user trim that cuts more, or a larger scale, wins over the fit."""
    configured = ImageConfig(scale=20, trim=TrimConfig(left=35, right=0, top=0, bottom=0))
    map_data = await _render(ijai, fit_to_explored=True, image_config=configured)

    # Left: the user's 35% (70 px) beats the fit's 45 px, leaving 45 x 50 pixels. Scale: 20 beats 960 / 50.
    assert map_data.image.data.size == (45 * 20, 50 * 20)


async def test_fit_icons_keep_the_configured_scale(ijai) -> None:
    """Icons zoom by how much the fit enlarged the map beyond the configured scale, not by the scale itself."""
    full = await _render(ijai, image_config=ImageConfig(scale=2))
    fitted = await _render(ijai, fit_to_explored=True, image_config=ImageConfig(scale=2))
    left, _, right, _ = _box(full.image.data, CHARGER)
    f_left, _, f_right, _ = _box(fitted.image.data, CHARGER)

    assert (f_right - f_left) / (right - left) == pytest.approx(FIT_SCALE / 2, rel=0.2)


@pytest.mark.parametrize(
    "island",
    [None, (0, 0, CANVAS - 1, CANVAS - 1), (0, 150, 30, CANVAS - 1)],
    ids=["nothing explored", "whole canvas", "corner"],
)
async def test_fit_edge_cases(ijai, island) -> None:
    """Nothing explored draws the full canvas; an island at the border is not trimmed past it."""
    map_data = await _render(ijai, build_island_map(island), fit_to_explored=True)
    if island is None:
        assert map_data.image.data.size == (CANVAS, CANVAS)
        return
    left, bottom, right, top = island
    width = min(CANVAS - 1, right + 15) - max(0, left - 15) + 1
    height = min(CANVAS - 1, top + 15) - max(0, bottom - 15) + 1
    scale = max(1, 960 / max(width, height))
    assert map_data.image.data.size == (int(width * scale), int(height * scale))
