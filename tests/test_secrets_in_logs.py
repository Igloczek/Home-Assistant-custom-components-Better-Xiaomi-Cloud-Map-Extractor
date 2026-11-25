"""Users paste debug logs into public issues: no session secret may ever be logged (#778).

Every login path and the map download run at DEBUG level against the fake cloud, then the whole log
is searched for each secret the fake handed out.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import json
import logging
import re
import struct
import traceback
import zlib
from unittest.mock import patch

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from miio.exceptions import DeviceException
from vacuum_map_parser_dreame.map_data_parser import DreameMapDataParser

from custom_components.better_xiaomi_cloud_map_extractor.connector import XiaomiCloudMapExtractorConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    CaptchaRequiredException,
    FailedConnectionException,
    FailedLoginException,
    TwoFactorAuthRequiredException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumApi
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_dreame import DreameCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

from . import log_secrets
from .fake_xiaomi import ACCOUNT, API, STS, USER_ID, FakeResponse, FakeXiaomiCloud, connection_timeout
from .ijai_map import MAC as IJAI_MAC
from .ijai_map import WIFI_SN, encrypt_map, map_key
from .log_secrets import MAP_URL_SIGNATURE, PASSWORD
from .test_cloud_session import stored_config
from .test_session_lifecycle import (  # noqa: F401
    DEVICE_ID,
    MAP_URL,
    SessionSaver,
    make_connector,
    map_configuration,
    no_map_parsing,
    stored_session,
    vacuum_cloud,
)

# The real parser, not the stub `no_map_parsing` puts in for the Viomi tests.
DREAME_DECODE_AND_PARSE = DreameCloudVacuum.decode_and_parse
IJAI_MODEL = "ijai.vacuum.v3"
IJAI_DEVICE_TOKEN = "f00dfeed" * 4
IJAI_SECRETS = {
    "device token": IJAI_DEVICE_TOKEN,
    "map key": map_key(IJAI_MODEL, WIFI_SN, USER_ID, DEVICE_ID, IJAI_MAC).hex(),
}


@pytest.fixture
def debug_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def assert_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    log_secrets.assert_no_secrets(caplog, IJAI_SECRETS)


async def test_password_login_and_captcha(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.password_login("captcha")
    cloud.pass_token_login(accepted=False)
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    with pytest.raises(CaptchaRequiredException):
        await connector.login_with_credentials("user@example.com", PASSWORD)

    cloud.password_login("ok")
    cloud.sts_issuing("captcha-service-token")
    await connector.continue_login_with_captcha("abcd")
    assert connector.has_service_token()
    assert_no_secrets(debug_log)


async def test_email_verification(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("two_factor")
    cloud.email_verification("verified-service-token")
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    with pytest.raises(TwoFactorAuthRequiredException) as verification:
        await connector.login_with_credentials("user@example.com", PASSWORD)

    assert await connector.continue_login_with_two_factor("123456", verification.value) is True
    assert connector.has_service_token()
    assert_no_secrets(debug_log)


async def test_qr_login(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token")
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")

    await connector.login_with_qr_get_code()
    await connector.login_with_qr_wait_for_completion()

    assert connector.has_service_token()
    assert_no_secrets(debug_log)


async def test_renewal_and_map_download(debug_log) -> None:
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.pass_token_login()
    cloud.sts_issuing("renewed-service-token")
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(hours=6)), SessionSaver())

    data = await connector.get_data()

    assert data.map_data_raw == b"raw-map-bytes"
    assert cloud.calls("https://account.xiaomi.com/pass/serviceLogin")
    assert_no_secrets(debug_log)


async def test_renewal_from_restored_config(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login()
    cloud.sts_issuing("renewed-service-token")
    connector = await XiaomiCloudConnector.from_config(stored_config(), cloud.session_creator)

    assert await connector.renew_session() is True
    assert_no_secrets(debug_log)


# Error pages echo what they were sent; failure branches must not log their bodies.


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, FailedConnectionException), (403, FailedLoginException)],
    ids=["outage", "refused"],
)
async def test_failed_qr_long_poll(debug_log, status: int, expected: type[Exception]) -> None:
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token")
    cloud.on("GET", f"{ACCOUNT}/longPolling/poll",
             lambda r: FakeResponse(status=status, body=f'{{"code": 70016, "desc": "error for {r.url}"}}'))
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    await connector.login_with_qr_get_code()

    with pytest.raises(expected):
        await connector.login_with_qr_wait_for_completion()
    assert_no_secrets(debug_log)


@pytest.mark.usefixtures("map_connector")
@pytest.mark.parametrize("approved", [True, False], ids=["approved", "refused"])
async def test_qr_login_flow(hass, debug_log, approved: bool) -> None:
    """The whole QR config flow, as Home Assistant runs it, incl. the login link it resolves."""
    cloud = vacuum_cloud()
    cloud.qr_login("qr-service-token")
    if not approved:
        cloud.on("GET", f"{ACCOUNT}/longPolling/poll",
                 lambda r: FakeResponse(status=200, body=f'{{"code": 70016, "desc": "refused {r.url}"}}'))
    with (
        patch(
            "custom_components.better_xiaomi_cloud_map_extractor.flow.login.async_create_clientsession",
            lambda hass: cloud.session_creator(),
        ),
        patch("custom_components.better_xiaomi_cloud_map_extractor.flow.vacuum.save_connector_config"),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "auth_qr"})
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        if approved:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"host": "192.168.1.14", "token": "0" * 32, "used_map_api": "VIOMI"}
            )
            await hass.async_block_till_done()

    assert result["type"] is (FlowResultType.CREATE_ENTRY if approved else FlowResultType.FORM)
    assert_no_secrets(debug_log)


async def test_failed_email_verification(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("two_factor")
    cloud.on("POST", f"{ACCOUNT}/identity/auth/verifyEmail",
             lambda r: FakeResponse(status=500, body=f"error for {r.url}"))
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    with pytest.raises(TwoFactorAuthRequiredException) as verification:
        await connector.login_with_credentials("user@example.com", PASSWORD)

    with pytest.raises(FailedConnectionException):
        await connector.continue_login_with_two_factor("123456", verification.value)
    assert_no_secrets(debug_log)


async def test_refused_map_download(debug_log) -> None:
    """S3-style storage answers a bad signature with a page that quotes it."""
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.on("GET", MAP_URL, lambda r: FakeResponse(status=403, body=f"SignatureDoesNotMatch {r.url}"))
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    await connector.get_data()
    assert_no_secrets(debug_log)


@pytest.mark.parametrize("vacuum_on_lan", [True, False], ids=["serial from the vacuum", "serial from the cloud"])
async def test_ijai_map_download(debug_log, vacuum_on_lan: bool) -> None:
    """The Ijai map key is built from the device token's vacuum: neither may reach the log."""
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.encrypted_api(
        "/v2/home/home_device_list",
        {"result": {"device_info": [{
            "did": DEVICE_ID, "name": "Ijai", "model": IJAI_MODEL, "token": IJAI_DEVICE_TOKEN,
            "localip": "192.0.2.10", "mac": IJAI_MAC,
        }]}},
    )
    cloud.encrypted_api(
        "/v2/home/get_interim_file_url_pro", {"result": {"url": f"{MAP_URL}?Expires=1&Signature={MAP_URL_SIGNATURE}"}}
    )
    cloud.encrypted_api(f"/v2/home/rpc/{DEVICE_ID}", {"result": [
        {"did": DEVICE_ID, "siid": 1, "piid": 3, "code": 0, "value": WIFI_SN},
    ]})
    raw_map = encrypt_map(IJAI_MODEL, device_id=DEVICE_ID, owner_id=USER_ID)
    cloud.on("GET", MAP_URL, lambda r: FakeResponse(body=raw_map))
    config = dataclasses.replace(
        map_configuration(), used_api=VacuumApi.IJAI, model=IJAI_MODEL, token=IJAI_DEVICE_TOKEN, mac=IJAI_MAC,
        host="192.0.2.10",
    )
    connector = XiaomiCloudMapExtractorConnector(
        cloud.session_creator, config, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver()
    )

    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai.MiotDevice"
    ) as miot:
        if vacuum_on_lan:
            miot.return_value.get_property_by.return_value = [{"siid": 1, "piid": 3, "code": 0, "value": WIFI_SN}]
        else:
            miot.return_value.get_property_by.side_effect = DeviceException("Unable to discover the device")
        data = await connector.get_data()

    assert data.map_data is not None and data.map_data.rooms
    assert bool(cloud.calls(f"https://de.api.io.mi.com/app/v2/home/rpc/{DEVICE_ID}")) is not vacuum_on_lan
    assert_no_secrets(debug_log)


# aiohttp quotes the whole request URL in its errors, and the encrypted API and STS calls carry the
# session's secrets in the query string. The message reaches the UI ("Failed to update") and the log.


def assert_clean_exception(err: BaseException, caplog: pytest.LogCaptureFixture) -> None:
    """Neither the message nor a logged traceback of `err` carries a secret or a URL's query string."""
    logging.getLogger(__name__).error("Update failed", exc_info=err)
    assert not re.search(r"https?://\S+\?(?!<redacted>)", str(err)), str(err)
    log_secrets.assert_no_secrets(caplog, IJAI_SECRETS, str(err), "".join(traceback.format_exception(err)))


async def test_api_timeout_message(debug_log) -> None:
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.on("POST", f"{API}/v2/homeroom/gethome", connection_timeout)
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    with pytest.raises(FailedConnectionException) as err:
        await connector.get_data()

    assert "de.api.io.mi.com/app/v2/homeroom/gethome" in str(err.value), "the message still says what failed"
    assert_clean_exception(err.value, debug_log)


async def test_qr_login_sts_timeout_message(debug_log) -> None:
    """The STS link of a finished login carries `clientSign`."""
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token")
    cloud.on("GET", STS, connection_timeout)
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    await connector.login_with_qr_get_code()

    with pytest.raises(FailedConnectionException) as err:
        await connector.login_with_qr_wait_for_completion()

    assert_clean_exception(err.value, debug_log)


DREAME_MODEL = "dreame.vacuum.r2209"  # encrypted maps: the key comes with the map name
DREAME_MAP_KEY = "made-up-dreame-map-key"


def encrypt_dreame_map(model: str, key: str) -> bytes:
    """A 1x1 I-frame, encrypted the way the encrypted Dreame models upload it."""
    header = struct.pack("<hhB3h3h5h", 0, 0, 73, 0, 0, 0, 0, 0, 0, 50, 1, 1, 0, 0)
    frame = header + b"\x00" + json.dumps({}).encode()
    aes_key = hashlib.sha256(key.encode()).hexdigest()[:32].encode()
    iv = DreameMapDataParser.IVs[model].encode()
    return base64.b64encode(AES.new(aes_key, AES.MODE_CBC, iv=iv).encrypt(pad(zlib.compress(frame), 16)))


async def test_dreame_encrypted_map_download(debug_log) -> None:
    """The map key arrives with the map name and goes to the parser: neither may log it."""
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.encrypted_api(
        "/v2/home/home_device_list",
        {"result": {"device_info": [{
            "did": DEVICE_ID, "name": "Dreame", "model": DREAME_MODEL, "token": "0" * 32,
            "localip": "192.0.2.10", "mac": "AA:BB:CC:DD:EE:01",
        }]}},
    )
    cloud.encrypted_api(f"/v2/home/rpc/{DEVICE_ID}", {"result": {"out": [
        {"piid": 1, "value": 0},
        {"piid": 3, "value": f"{USER_ID}/{DEVICE_ID}/dreame-map-0,{DREAME_MAP_KEY}"},
    ]}})
    cloud.encrypted_api(
        "/v2/home/get_interim_file_url", {"result": {"url": f"{MAP_URL}?Expires=1&Signature={MAP_URL_SIGNATURE}"}}
    )
    cloud.on("GET", MAP_URL, lambda r: FakeResponse(body=encrypt_dreame_map(DREAME_MODEL, DREAME_MAP_KEY)))
    config = dataclasses.replace(
        map_configuration(), used_api=VacuumApi.DREAME, model=DREAME_MODEL, host="192.0.2.10",
    )
    connector = XiaomiCloudMapExtractorConnector(
        cloud.session_creator, config, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver()
    )

    with patch.object(DreameCloudVacuum, "decode_and_parse", DREAME_DECODE_AND_PARSE):
        data = await connector.get_data()

    assert data.map_data is not None and data.map_data.map_name == "dreame-map-0"
    log_secrets.assert_no_secrets(debug_log, {"map key": DREAME_MAP_KEY})
