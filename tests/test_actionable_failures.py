"""Every failure ends in something the user can act on: never "Unknown error", never an unhandled exception.

Each exception the connector (or a library under it) can raise is driven through every flow step that
calls the connector, and through the coordinator. The result must be a translated, specific error,
abort or update failure; `flow_results_render` in conftest checks the texts exist.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientConnectionError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from miio.exceptions import DeviceException

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    CaptchaRequiredException,
    DeviceNotFoundException,
    FailedConnectionException,
    FailedLoginException,
    FailedMapDownloadException,
    FailedMapParseException,
    MapSavingDisabledException,
    InvalidCredentialsException,
    InvalidDeviceTokenException,
    InvalidTwoFactorCodeException,
    QrLoginTimeoutException,
    RetryLaterException,
    SessionExpiredException,
    TwoFactorAuthRequiredException,
    UnexpectedResponseError,
    VerificationIncompleteException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN
from custom_components.better_xiaomi_cloud_map_extractor.coordinator import (
    XiaomiCloudMapExtractorDataUpdateCoordinator,
)

from .conftest import make_device
from .const import PASSWORD, USERNAME
from .fake_xiaomi import API, FakeResponse
from .test_session_lifecycle import DEVICE_ID, vacuum_cloud

pytestmark = pytest.mark.usefixtures("no_store")

TRANSLATIONS = json.loads(
    (Path(__file__).parents[1] / "custom_components/better_xiaomi_cloud_map_extractor/translations/en.json").read_text()
)

# Everything a login call can raise, and the error the login form must show for it.
# Captcha and identity verification have their own steps (tests/test_config_flow.py).
LOGIN_ERRORS = {
    "wrong password": (InvalidCredentialsException(), "cloud_login_error"),
    "login refused": (FailedLoginException(), "cloud_login_error"),
    "session refused": (SessionExpiredException(), "cloud_login_error"),
    "Xiaomi unreachable": (FailedConnectionException(TimeoutError()), "cannot_connect"),
    "Xiaomi down": (FailedConnectionException(UnexpectedResponseError(502)), "cannot_connect"),
    "login throttled": (RetryLaterException(), "cannot_connect"),
    "raw timeout": (TimeoutError(), "cannot_connect"),
    "raw connection error": (ClientConnectionError(), "cannot_connect"),
    "unexpected answer": (KeyError("ssecurity"), "unexpected_response"),
    "other connector error": (FailedMapDownloadException(), "cloud_login_error"),
}

# A wrong password found out at the captcha step can only be fixed on the credentials form (#712).
CAPTCHA_ERROR_STEP = {"wrong password": "auth_credentials"}

# The verification step adds what only checking a code can raise.
TWO_FACTOR_ERRORS = {
    **LOGIN_ERRORS,
    "code refused": (InvalidTwoFactorCodeException(), "invalid_two_factor_code"),
    "code accepted, no session": (VerificationIncompleteException(), "verification_incomplete"),
}

# Everything the device list right after the login can raise, and the error its retry form shows.
DEVICE_LIST_ERRORS = {
    "Xiaomi unreachable": (FailedConnectionException(TimeoutError()), "cannot_connect"),
    "raw timeout": (TimeoutError(), "cannot_connect"),
    "raw connection error": (ClientConnectionError(), "cannot_connect"),
    "session refused": (SessionExpiredException(), "session_rejected"),
    "login refused": (FailedLoginException(), "session_rejected"),
    "unexpected answer": (KeyError("result"), "unexpected_response"),
    "other connector error": (DeviceNotFoundException(), "unexpected_response"),
}


async def _start(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "auth_credentials"})


async def _submit_credentials(hass: HomeAssistant, result: dict) -> dict:
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": PASSWORD}
    )


async def _to_captcha(hass: HomeAssistant, connector: MagicMock) -> dict:
    connector.login_with_credentials.side_effect = CaptchaRequiredException("https://captcha", b"png")
    result = await _submit_credentials(hass, await _start(hass))
    assert result["step_id"] == "auth_captcha"
    return result


async def _to_two_factor(hass: HomeAssistant, connector: MagicMock) -> dict:
    connector.login_with_credentials.side_effect = TwoFactorAuthRequiredException("https://2fa", {}, "context")
    result = await _submit_credentials(hass, await _start(hass))
    assert result["step_id"] == "auth_two_factor"
    return result


def _assert_form_error(result: dict, step_id: str, error: str) -> None:
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == step_id
    assert error in result["errors"].values()


@pytest.mark.parametrize(("raised", "error"), LOGIN_ERRORS.values(), ids=LOGIN_ERRORS.keys())
async def test_credentials_login_errors(
    hass: HomeAssistant, cloud_connector: MagicMock, raised: Exception, error: str
) -> None:
    cloud_connector.login_with_credentials.side_effect = raised

    result = await _submit_credentials(hass, await _start(hass))

    _assert_form_error(result, "auth_credentials", error)


@pytest.mark.parametrize(("raised", "error"), LOGIN_ERRORS.values(), ids=LOGIN_ERRORS.keys())
async def test_captcha_login_errors(
    hass: HomeAssistant, cloud_connector: MagicMock, raised: Exception, error: str, request: pytest.FixtureRequest
) -> None:
    result = await _to_captcha(hass, cloud_connector)
    cloud_connector.continue_login_with_captcha = AsyncMock(side_effect=raised)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha_code": "abcd"})

    _assert_form_error(result, CAPTCHA_ERROR_STEP.get(request.node.callspec.id, "auth_captcha"), error)


@pytest.mark.parametrize(("raised", "error"), TWO_FACTOR_ERRORS.values(), ids=TWO_FACTOR_ERRORS.keys())
async def test_two_factor_login_errors(
    hass: HomeAssistant, cloud_connector: MagicMock, raised: Exception, error: str
) -> None:
    result = await _to_two_factor(hass, cloud_connector)
    cloud_connector.continue_login_with_two_factor = AsyncMock(side_effect=raised)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"two_factor_code": "123456"})

    _assert_form_error(result, "auth_two_factor", error)


async def _login_by(hass: HomeAssistant, connector: MagicMock, method: str) -> dict:
    if method == "credentials":
        return await _submit_credentials(hass, await _start(hass))
    if method == "captcha":
        result = await _to_captcha(hass, connector)
        connector.continue_login_with_captcha = AsyncMock(return_value="user_id")
        return await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha_code": "abcd"})
    result = await _to_two_factor(hass, connector)
    connector.continue_login_with_two_factor = AsyncMock(return_value=True)
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"two_factor_code": "123456"})


@pytest.mark.parametrize("method", ["credentials", "captcha", "two_factor"])
@pytest.mark.parametrize(("raised", "error"), DEVICE_LIST_ERRORS.values(), ids=DEVICE_LIST_ERRORS.keys())
async def test_device_list_errors_after_login(
    hass: HomeAssistant, cloud_connector: MagicMock, method: str, raised: Exception, error: str
) -> None:
    """The login worked, the device list did not: say why, and one click tries again."""
    cloud_connector.get_devices.side_effect = [raised, [make_device()]]
    result = await _login_by(hass, cloud_connector, method)
    cloud_connector.login_with_credentials.side_effect = None

    _assert_form_error(result, "devices_failed", error)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    if error == "session_rejected":
        # Retrying with a session Xiaomi refused is pointless: back to choosing how to log in.
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "auth_method_selection"
    else:
        assert result["step_id"] == "confirm_data"


async def test_roborock_validation_library_error(hass: HomeAssistant, cloud_connector: MagicMock) -> None:
    """python-miio raising while checking the vacuum is a form error on the confirm step."""
    cloud_connector.get_devices.return_value = [make_device(model="roborock.vacuum.s5")]
    result = await _submit_credentials(hass, await _start(hass))
    from unittest.mock import patch

    with patch(
        "custom_components.better_xiaomi_cloud_map_extractor.config_flow.RoborockVacuum.status",
        side_effect=DeviceException("timeout"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.168.1.50", "token": "0" * 32, "used_map_api": "ROBOROCK"}
        )

    _assert_form_error(result, "confirm_data", "invalid_vacuum")


# The device list across every region: a region that fails must not read as "no vacuums".


def _other_regions_empty(cloud) -> None:
    for server in ("cn", "us", "ru", "tw", "sg", "in", "i2"):
        host = "https://api.io.mi.com/app" if server == "cn" else f"https://{server}.api.io.mi.com/app"
        cloud.on("POST", f"{host}/v2/homeroom/gethome",
                 lambda r: FakeResponse(status=200, body='{"code": 0, "result": {"homelist": []}}'))


async def _logged_in(cloud) -> XiaomiCloudConnector:
    from .test_session_lifecycle import stored_session
    import datetime

    session = stored_session(expires_in=datetime.timedelta(days=20))
    cloud.valid_service_tokens.add(session.service_token)
    return await XiaomiCloudConnector.from_config(session, cloud.session_creator)


async def test_device_list_finds_vacuum_although_other_regions_fail() -> None:
    cloud = vacuum_cloud()  # only "de" answers; every other region fails
    connector = await _logged_in(cloud)

    assert [d.device_id for d in await connector.get_devices()] == [DEVICE_ID]


async def test_device_list_of_an_account_without_vacuums_is_empty() -> None:
    cloud = vacuum_cloud()
    _other_regions_empty(cloud)
    cloud.encrypted_api("/v2/homeroom/gethome", {"result": {"homelist": []}})
    connector = await _logged_in(cloud)

    assert await connector.get_devices() == []


async def test_device_list_with_the_region_down_is_an_outage_not_an_empty_account() -> None:
    cloud = vacuum_cloud()
    _other_regions_empty(cloud)
    cloud.on("POST", f"{API}/v2/homeroom/gethome", lambda r: FakeResponse(status=502, body="<html/>"))
    connector = await _logged_in(cloud)

    with pytest.raises(FailedConnectionException):
        await connector.get_devices()


async def test_device_list_with_a_broken_home_answer_is_an_outage() -> None:
    cloud = vacuum_cloud()
    _other_regions_empty(cloud)
    cloud.encrypted_api("/v2/home/home_device_list", {"code": 0})  # no "result"
    connector = await _logged_in(cloud)

    with pytest.raises(FailedConnectionException):
        await connector.get_devices()


# The coordinator: everything get_data can raise ends as a translated update failure or reauth.

COORDINATOR_ERRORS = {
    "Xiaomi unreachable": (FailedConnectionException(TimeoutError()), UpdateFailed, "cloud_unavailable"),
    "login throttled": (RetryLaterException(), UpdateFailed, "login_retry_later"),
    "map download": (FailedMapDownloadException(), UpdateFailed, "map_download_failed"),
    "map parse": (FailedMapParseException(), UpdateFailed, "map_parse_failed"),
    "map saving off (Ijai)": (MapSavingDisabledException(), UpdateFailed, "map_saving_disabled"),
    "QR timeout (login error)": (QrLoginTimeoutException(), ConfigEntryAuthFailed, "session_expired"),
    "raw timeout": (TimeoutError(), UpdateFailed, "cloud_unavailable"),
    "raw connection error": (ClientConnectionError(), UpdateFailed, "cloud_unavailable"),
    "vacuum unreachable (python-miio)": (DeviceException("timeout"), UpdateFailed, "vacuum_unreachable"),
    "unexpected answer": (KeyError("result"), UpdateFailed, "unexpected_error"),
    "invalid device token": (InvalidDeviceTokenException(), UpdateFailed, "vacuum_token_refused"),
    "vacuum not in region": (DeviceNotFoundException(), UpdateFailed, "vacuum_not_in_region"),
    "login refused": (InvalidCredentialsException(), ConfigEntryAuthFailed, "invalid_credentials"),
}


@pytest.mark.parametrize(
    ("raised", "expected", "key"), COORDINATOR_ERRORS.values(), ids=COORDINATOR_ERRORS.keys()
)
async def test_coordinator_failures_are_translated(
    hass: HomeAssistant, raised: Exception, expected: type[Exception], key: str
) -> None:
    connector = MagicMock()
    connector.get_data = AsyncMock(side_effect=raised)
    coordinator = XiaomiCloudMapExtractorDataUpdateCoordinator(hass, connector)

    with pytest.raises(expected) as failure:
        await coordinator.update_data()

    assert failure.value.translation_domain == DOMAIN
    assert failure.value.translation_key == key
    assert key in TRANSLATIONS["exceptions"]


async def test_map_saving_off_says_what_to_do(hass: HomeAssistant) -> None:
    """The update failure names the one fix: map saving in the Xiaomi Home app (upstream #759)."""
    connector = MagicMock()
    connector.get_data = AsyncMock(side_effect=MapSavingDisabledException())
    coordinator = XiaomiCloudMapExtractorDataUpdateCoordinator(hass, connector)

    with pytest.raises(UpdateFailed) as failure:
        await coordinator.update_data()

    message = TRANSLATIONS["exceptions"][failure.value.translation_key]["message"]
    assert "turn on map saving in the xiaomi home app" in message.lower()


# Re-authentication goes through the same login steps; its own entry point is the saved credentials.


async def _reauth_with_saved_credentials(hass: HomeAssistant) -> dict:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .const import ENTRY_DATA, MAC

    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["menu_options"][0] == "auth_saved_credentials"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_saved_credentials"}
    )


@pytest.mark.parametrize(("raised", "error"), LOGIN_ERRORS.values(), ids=LOGIN_ERRORS.keys())
async def test_reauth_login_errors(
    hass: HomeAssistant, cloud_connector: MagicMock, raised: Exception, error: str
) -> None:
    cloud_connector.login_with_credentials.side_effect = raised

    result = await _reauth_with_saved_credentials(hass)

    _assert_form_error(result, "auth_credentials", error)


@pytest.mark.parametrize(("raised", "error"), DEVICE_LIST_ERRORS.values(), ids=DEVICE_LIST_ERRORS.keys())
async def test_reauth_device_list_errors(
    hass: HomeAssistant, cloud_connector: MagicMock, raised: Exception, error: str
) -> None:
    cloud_connector.get_devices.side_effect = [raised, [make_device()]]

    result = await _reauth_with_saved_credentials(hass)

    _assert_form_error(result, "devices_failed", error)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == ("auth_method_selection" if error == "session_rejected" else "confirm_data")


async def test_session_that_cannot_be_saved_shows_error_and_retries(
    hass: HomeAssistant, cloud_connector: MagicMock, no_store: AsyncMock
) -> None:
    """Without the stored session the entry could not log in after setup (QR logins have no password)."""
    no_store.side_effect = [OSError("No space left on device"), None]
    result = await _submit_credentials(hass, await _start(hass))
    user_input = {"host": "192.168.1.50", "token": "0" * 32, "used_map_api": "VIOMI"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    _assert_form_error(result, "confirm_data", "session_not_saved")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
