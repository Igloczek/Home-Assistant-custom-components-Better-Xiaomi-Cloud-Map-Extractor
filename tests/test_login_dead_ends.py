"""Logins that used to end nowhere (upstream #700, #712, #780 and the case for PR #793).

Every scenario runs the config flow as Home Assistant does, against the fake Xiaomi cloud answering
the way the real one does in those reports and in XiaomiGateway3 and hass-xiaomi-miot: passport JSON
behind `&&&START&&&`, the identity session cookie, verify's `location` in its different shapes, a
captcha that stays required after a wrong password, and regions that never answer. Each one must end
in a form the user can act on, without a secret in the log.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor.const import (
    CONF_CAPTCHA_CODE,
    CONF_TWO_FACTOR_CODE,
    DOMAIN,
)

from .const import PASSWORD, USERNAME
from .fake_xiaomi import (
    ACCOUNT,
    CAPTCHA_SOLUTION,
    FLAG_EMAIL,
    FLAG_PHONE,
    SERVICE_LOGIN_AUTH2,
    VERIFICATION_CODE,
    VERIFY_FINISH,
    FakeResponse,
    FakeXiaomiCloud,
)
from .log_secrets import assert_no_secrets
from .test_reauth_session import make_entry
from .test_session_lifecycle import vacuum_cloud

PACKAGE = "custom_components.better_xiaomi_cloud_map_extractor"
# Longer than any timeout this integration should wait for; far shorter than aiohttp's 5 minute default.
FLOW_STEP_DEADLINE = 5
VACUUM = {"host": "192.168.1.14", "token": "0" * 32, "used_map_api": "VIOMI"}

pytestmark = pytest.mark.usefixtures("no_store")


@pytest.fixture(autouse=True)
def debug_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture
def cloud() -> Generator[FakeXiaomiCloud]:
    """One Viomi vacuum on `de`; the flow's sessions all go to this fake."""
    cloud = vacuum_cloud()
    cloud.pass_token_login()
    cloud.sts_issuing("password-service-token")
    with patch(f"{PACKAGE}.flow.login.async_create_clientsession", lambda hass: cloud.session_creator()):
        yield cloud


async def configure(hass: HomeAssistant, result: dict, user_input: dict | None = None) -> dict:
    async with asyncio.timeout(FLOW_STEP_DEADLINE):
        return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def submit_credentials(hass: HomeAssistant, password: str = PASSWORD) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await configure(hass, result, {"next_step_id": "auth_credentials"})
    return await configure(hass, result, {"username": USERNAME, "password": password})


def assert_step(result: dict, step_id: str, errors: dict | None = None) -> None:
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == step_id, result
    assert (result.get("errors") or {}) == (errors or {}), result


# --- #780: the code is accepted, then "Unable to determine finish location after verifyEmail" -------


@pytest.mark.parametrize("finish", VERIFY_FINISH)
async def test_verification_code_finishes_the_login(hass: HomeAssistant, cloud: FakeXiaomiCloud, finish, debug_log):
    cloud.password_login("two_factor")
    cloud.email_verification("verified-service-token", finish=finish)

    result = await submit_credentials(hass)
    assert_step(result, "auth_two_factor")
    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: VERIFICATION_CODE})

    assert_step(result, "confirm_data")
    assert "verified-service-token" in cloud.valid_service_tokens
    assert_no_secrets(debug_log)


@pytest.mark.usefixtures("map_connector")
async def test_verification_by_sms_finishes_the_setup(hass: HomeAssistant, cloud: FakeXiaomiCloud, debug_log):
    """An account whose verification method is the phone gets an SMS, not an email that never comes."""
    cloud.password_login("two_factor")
    cloud.identity_verification(flag=FLAG_PHONE)
    cloud.email_verification("verified-service-token", flag=FLAG_PHONE)

    result = await submit_credentials(hass)
    assert_step(result, "auth_two_factor")
    assert cloud.calls(f"{ACCOUNT}/identity/auth/sendPhoneTicket")
    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: VERIFICATION_CODE})
    result = await configure(hass, result, VACUUM)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert_no_secrets(debug_log)


async def test_wrong_verification_code_asks_for_the_code_again(hass: HomeAssistant, cloud: FakeXiaomiCloud):
    cloud.password_login("two_factor")
    cloud.email_verification("verified-service-token")

    result = await submit_credentials(hass)
    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: "000000"})
    assert_step(result, "auth_two_factor", {CONF_TWO_FACTOR_CODE: "invalid_two_factor_code"})

    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: VERIFICATION_CODE})
    assert_step(result, "confirm_data")


@pytest.mark.parametrize("send_code", [87001, 70022], ids=["captcha", "refused"])
async def test_code_that_could_not_be_sent_says_so(hass: HomeAssistant, cloud: FakeXiaomiCloud, send_code: int):
    """Xiaomi wants a captcha (or refuses) before it sends the code: nobody should wait for an email."""
    cloud.password_login("two_factor")
    cloud.identity_verification(flag=FLAG_EMAIL, send_code=send_code)

    result = await submit_credentials(hass)

    assert_step(result, "auth_two_factor", {"base": "two_factor_code_not_sent"})


async def test_accepted_code_without_a_session_is_explained(hass: HomeAssistant, cloud: FakeXiaomiCloud):
    """Xiaomi accepted the code but its redirects never issued a session: not a credentials error."""
    cloud.password_login("two_factor")
    cloud.email_verification("verified-service-token")
    cloud.on("GET", f"{ACCOUNT}/identity/result/check", lambda r: FakeResponse(body="<html>Done</html>"))
    cloud.pass_token_login(accepted=False)

    result = await submit_credentials(hass)
    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: VERIFICATION_CODE})

    assert_step(result, "auth_two_factor", {"base": "verification_incomplete"})


# --- #700: the code is entered, nothing happens for minutes, then "Unexpected error" ----------------


async def test_unanswering_regions_do_not_stall_the_setup(hass: HomeAssistant, cloud: FakeXiaomiCloud, debug_log):
    """`us` never answers from Russia (#700) and `ru` is behind an ISP block page: `de` still lists the vacuum.

    Without a request timeout aiohttp waits 5 minutes for the hanging region; the flow must move on.
    """
    cloud.password_login("two_factor")
    cloud.email_verification("verified-service-token")
    cloud.hanging_hosts.add("us.api.io.mi.com")
    cloud.on("POST", "https://ru.api.io.mi.com/app/v2/homeroom/gethome",
             lambda r: FakeResponse(status=423, body="<html>Locked</html>"))

    result = await submit_credentials(hass)
    result = await configure(hass, result, {CONF_TWO_FACTOR_CODE: VERIFICATION_CODE})

    assert_step(result, "confirm_data")
    timeouts = [r.timeout for r in cloud.requests if r.url.host == "us.api.io.mi.com"]
    assert timeouts and all(t is not None and t.total <= 30 for t in timeouts)
    assert_no_secrets(debug_log)


async def test_every_region_unanswering_offers_to_try_again(hass: HomeAssistant, cloud: FakeXiaomiCloud):
    cloud.password_login("ok")
    cloud.hanging_hosts.update(f"{region}.api.io.mi.com" for region in ("de", "us", "ru", "sg", "in", "i2", "tw"))
    cloud.hanging_hosts.add("api.io.mi.com")

    result = await submit_credentials(hass)

    assert_step(result, "devices_failed", {"base": "cannot_connect"})


# --- #712: after a captcha, a retry only ever asks for the captcha ----------------------------------


async def test_wrong_password_behind_a_captcha_asks_for_the_password_again(
    hass: HomeAssistant, cloud: FakeXiaomiCloud, debug_log
):
    cloud.captcha_account(PASSWORD)

    result = await submit_credentials(hass, password="mistyped-password")
    assert_step(result, "auth_captcha")
    result = await configure(hass, result, {CONF_CAPTCHA_CODE: "wrong"})
    assert_step(result, "auth_captcha", {CONF_CAPTCHA_CODE: "invalid_captcha"})

    result = await configure(hass, result, {CONF_CAPTCHA_CODE: CAPTCHA_SOLUTION})
    assert_step(result, "auth_credentials", {"base": "cloud_login_error"})

    result = await configure(hass, result, {"username": USERNAME, "password": PASSWORD})
    assert_step(result, "auth_captcha")
    result = await configure(hass, result, {CONF_CAPTCHA_CODE: CAPTCHA_SOLUTION})
    assert_step(result, "confirm_data")
    assert cloud.password_login_count() == 5
    assert_no_secrets(debug_log, {"mistyped password": "mistyped-password"})


async def test_reauth_with_a_changed_password_behind_a_captcha(hass: HomeAssistant, cloud: FakeXiaomiCloud):
    """The saved password no longer works and Xiaomi asks for a captcha: the user gets to type the new one."""
    cloud.captcha_account("the-new-password")
    entry: MockConfigEntry = make_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await configure(hass, result, {})
    result = await configure(hass, result, {"next_step_id": "auth_saved_credentials"})
    assert_step(result, "auth_captcha")
    result = await configure(hass, result, {CONF_CAPTCHA_CODE: CAPTCHA_SOLUTION})

    assert_step(result, "auth_credentials", {"base": "cloud_login_error"})
    result = await configure(hass, result, {"username": USERNAME, "password": "the-new-password"})
    result = await configure(hass, result, {CONF_CAPTCHA_CODE: CAPTCHA_SOLUTION})
    assert_step(result, "confirm_data")


async def test_captcha_is_sent_with_the_session_that_fetched_it(hass: HomeAssistant, cloud: FakeXiaomiCloud):
    cloud.captcha_account(PASSWORD)

    result = await submit_credentials(hass)
    result = await configure(hass, result, {CONF_CAPTCHA_CODE: CAPTCHA_SOLUTION})

    assert_step(result, "confirm_data")
    assert cloud.calls(SERVICE_LOGIN_AUTH2)[-1].cookies.get("ick")
