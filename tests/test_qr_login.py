"""QR code login, driven through Home Assistant's flow manager to the end against the fake cloud.

The progress step used to be tested only up to the QR code. Home Assistant 2026.9 re-runs a progress
step while it waits and only lets it end with `async_show_progress_done`, so these tests keep the long
poll open, re-run the step, then let the app "approve" the login and walk the flow to its entry.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientConnectionError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    FailedLoginException,
    QrLoginTimeoutException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector
from custom_components.better_xiaomi_cloud_map_extractor.const import CONF_USED_MAP_API, DOMAIN

from .fake_xiaomi import (
    BAD_GATEWAY_PAGE,
    C_USER_ID,
    PASS_TOKEN,
    QR_LINK,
    QR_LOGIN_URL,
    QR_LONG_POLL,
    SERVICE_LOGIN,
    STS,
    USER_ID,
    FakeResponse,
    FakeXiaomiCloud,
    json_response,
)
from .test_session_lifecycle import DEVICE_ID, vacuum_cloud

VACUUM_MAC = "60:1d:9d:3b:87:37"  # the vacuum vacuum_cloud() lists


def raising(error: BaseException):
    def handler(_request):
        raise error

    return handler


@pytest.fixture
def cloud() -> Generator[FakeXiaomiCloud]:
    """The fake Xiaomi cloud the config flow's connector talks to, with the app not yet scanning."""
    cloud = vacuum_cloud()
    cloud.qr_login("qr-service-token")
    cloud.gates[QR_LONG_POLL] = asyncio.Event()
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.config_flow.async_create_clientsession",
        lambda hass: cloud.session_creator(),
    ):
        yield cloud


@pytest.fixture
def saved_sessions() -> Generator[AsyncMock]:
    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.config_flow.save_connector_config",
        new=AsyncMock(),
    ) as save:
        yield save


pytestmark = pytest.mark.usefixtures("saved_sessions", "map_connector")


async def _start_qr(hass: HomeAssistant, result: dict | None = None) -> dict:
    if result is None:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "auth_qr"})


async def _scan(hass: HomeAssistant, cloud: FakeXiaomiCloud, result: dict) -> dict:
    """The app approves the login: the long poll answers and Home Assistant moves the flow on."""
    cloud.gates[QR_LONG_POLL].set()
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


def _assert_waiting_with_qr_code(result: dict) -> None:
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "auth_qr"
    placeholders = result["description_placeholders"]
    assert placeholders["qr_image_b64"] == base64.b64encode(b"\x89PNG-qr-image").decode()
    assert placeholders["login_link"].startswith(SERVICE_LOGIN)


async def _confirm(hass: HomeAssistant, result: dict) -> dict:
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "confirm_data"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.168.1.14", "token": "0" * 32, CONF_USED_MAP_API: "VIOMI"}
    )


async def test_qr_login_creates_entry_with_a_usable_session(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, saved_sessions: AsyncMock
) -> None:
    result = await _start_qr(hass)
    _assert_waiting_with_qr_code(result)

    # The frontend and the progress task re-run the step while the code waits to be scanned (#795).
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    _assert_waiting_with_qr_code(result)

    result = await _confirm(hass, await _scan(hass, cloud, result))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == VACUUM_MAC
    assert result["data"]["device_id"] == DEVICE_ID
    session = saved_sessions.await_args.args[2]
    assert (session.user_id, session.c_user_id, session.pass_token) == (USER_ID, C_USER_ID, PASS_TOKEN)
    assert session.service_token == "qr-service-token"

    # The stored session finds the vacuum again after a restart (#796: it lacked the cUserId).
    restored = await XiaomiCloudConnector.from_config(session, cloud.session_creator)
    assert [device.device_id for device in await restored.get_devices("de")] == [DEVICE_ID]


async def test_qr_login_link_opens_the_sign_in_page(hass: HomeAssistant, cloud: FakeXiaomiCloud) -> None:
    """A signed-out browser must get Xiaomi's sign-in page, not a JSON document (#766)."""
    result = await _start_qr(hass)

    link = URL(result["description_placeholders"]["login_link"])
    assert "_json" not in link.query
    # The followup and callback complete the long poll once the user signs in: they stay.
    assert link.query["callback"] == f"{STS}?d=qr"
    assert link.query["followup"].startswith(f"{SERVICE_LOGIN.rsplit('/pass', 1)[0]}/longPolling/followup")


@pytest.mark.parametrize(
    "link_answer",
    [
        FakeResponse(status=200, body="<html/>"),
        FakeResponse(status=302, headers={"Location": f"{SERVICE_LOGIN}?sid=xiaomiio"}),
    ],
    ids=["no redirect", "redirect without _json"],
)
async def test_qr_login_link_kept_when_there_is_nothing_to_fix(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, link_answer: FakeResponse
) -> None:
    cloud.on("GET", QR_LINK, lambda r: link_answer)
    result = await _start_qr(hass)

    assert result["description_placeholders"]["login_link"] == f"{QR_LINK}?ticket=qr-login-ticket"


async def test_qr_login_link_kept_when_xiaomi_cannot_be_reached(hass: HomeAssistant, cloud: FakeXiaomiCloud) -> None:
    cloud.on("GET", QR_LINK, raising(TimeoutError()))
    result = await _start_qr(hass)

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["description_placeholders"]["login_link"] == f"{QR_LINK}?ticket=qr-login-ticket"


async def test_qr_reauth_updates_the_entry(hass: HomeAssistant, cloud: FakeXiaomiCloud) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=VACUUM_MAC,
        data={
            "host": "192.168.1.14",
            "token": "0" * 32,
            "device_id": DEVICE_ID,
            "model": "viomi.vacuum.v8",
            "mac": VACUUM_MAC,
            "name": "Viomi V8",
            "username": None,
            "password": None,
            "server": "de",
            CONF_USED_MAP_API: "VIOMI",
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    result = await _start_qr(hass, result)
    _assert_waiting_with_qr_code(result)
    result = await _confirm(hass, await _scan(hass, cloud, result))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_qr_login_retries_a_long_poll_that_timed_out(hass: HomeAssistant, cloud: FakeXiaomiCloud) -> None:
    """Xiaomi holds the poll open; one that ends before the code expires is simply asked again."""
    answers = iter([TimeoutError(), None])

    def long_poll(request):
        if (error := next(answers)) is not None:
            raise error
        cloud.qr_scanned()
        return cloud.routes[("GET", QR_LONG_POLL)](request)

    cloud.qr_long_poll(long_poll)
    result = await _confirm(hass, await _scan(hass, cloud, await _start_qr(hass)))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(cloud.calls(QR_LONG_POLL)) == 2


QR_FAILURES = {
    "code expired": (lambda cloud: cloud.qr_login("qr-service-token", timeout=0),
                     raising(TimeoutError()), "qr_timeout"),
    "login refused": (None, lambda r: json_response({"code": 70016, "desc": "refused"}), "qr_rejected"),
    "no serviceToken": (None, None, "qr_rejected"),
    "connection lost": (None, raising(ClientConnectionError()), "cannot_connect"),
    "Xiaomi down": (None, lambda r: FakeResponse(status=502, body=BAD_GATEWAY_PAGE), "cannot_connect"),
    "not JSON": (None, lambda r: FakeResponse(status=200, body="<html/>"), "cannot_connect"),
    "a bug": (None, raising(RuntimeError("bug")), "qr_failed"),
}


@pytest.mark.parametrize(("setup", "long_poll", "error"), QR_FAILURES.values(), ids=QR_FAILURES.keys())
async def test_failed_qr_login_shows_an_error_and_a_new_code(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, setup, long_poll, error: str
) -> None:
    if setup is not None:
        setup(cloud)
    if long_poll is not None:
        cloud.qr_long_poll(long_poll)
    else:
        cloud.on("GET", STS, lambda r: FakeResponse(body="ok"))  # the session cookie never comes

    result = await _scan(hass, cloud, await _start_qr(hass))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_qr_failed"
    assert result["errors"] == {"base": error}

    # One click on submit shows a fresh code, and that one works.
    cloud.qr_login("qr-service-token")
    cloud.gates[QR_LONG_POLL] = asyncio.Event()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    _assert_waiting_with_qr_code(result)
    result = await _confirm(hass, await _scan(hass, cloud, result))
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("endpoint", "answer", "error"),
    [
        (QR_LOGIN_URL, lambda r: json_response({"code": 70002, "desc": "busy"}), "qr_code_unavailable"),
        (QR_LOGIN_URL, lambda r: FakeResponse(status=502, body=BAD_GATEWAY_PAGE), "cannot_connect"),
        (SERVICE_LOGIN, raising(ClientConnectionError()), "cannot_connect"),
        (SERVICE_LOGIN, lambda r: FakeResponse(status=200, body="<html/>"), "cannot_connect"),
    ],
    ids=["code refused", "code endpoint down", "connection lost", "not JSON"],
)
async def test_unavailable_qr_code_shows_an_error(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, endpoint: str, answer, error: str
) -> None:
    cloud.on("GET", endpoint, answer)

    result = await _start_qr(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_qr_failed"
    assert result["errors"] == {"base": error}


# Connector level: what the long poll may answer.


async def _connector_waiting(cloud: FakeXiaomiCloud) -> XiaomiCloudConnector:
    connector = XiaomiCloudConnector(cloud.session_creator)
    await connector.login_with_qr_get_code()
    return connector


async def test_long_poll_without_user_id_cookie_uses_default_lifetime() -> None:
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token")
    approved = cloud.routes[("GET", QR_LONG_POLL)]

    def without_cookies(request):
        response = approved(request)
        response.cookies.clear()
        return response

    cloud.qr_long_poll(without_cookies)
    connector = await _connector_waiting(cloud)

    await connector.login_with_qr_wait_for_completion()

    assert connector.has_service_token()
    assert not connector.is_session_expiring()


@pytest.mark.parametrize(
    ("long_poll", "expected"),
    [
        (raising(TimeoutError()), QrLoginTimeoutException),
        (lambda r: json_response({"code": 70016}), FailedLoginException),
        (lambda r: json_response({"code": 0}), FailedLoginException),
        (raising(ClientConnectionError()), FailedConnectionException),
    ],
    ids=["timeout", "refused", "incomplete answer", "connection lost"],
)
async def test_long_poll_failures_raise_connector_errors(long_poll, expected: type[Exception]) -> None:
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token", timeout=0)
    cloud.qr_long_poll(long_poll)
    connector = await _connector_waiting(cloud)

    with pytest.raises(expected):
        await connector.login_with_qr_wait_for_completion()
    assert not connector.has_service_token()
