"""Xiaomi being down is not Xiaomi saying no.

Only a JSON answer from Xiaomi that refuses the session or the password may lead to a password login
or a reauth. A 5xx, an HTML error page or an undecodable body is an outage: retry later, keep the
session, never ask the user to log in again (that is what makes Xiaomi demand a captcha).
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    InvalidCredentialsException,
    RetryLaterException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

from .fake_xiaomi import (
    API,
    PASS_TOKEN,
    SERVICE_LOGIN,
    SERVICE_LOGIN_AUTH2,
    STS,
    FakeResponse,
    FakeXiaomiCloud,
    bad_gateway,
    html_page,
)
from .test_cloud_session import stored_config
from .test_session_lifecycle import SessionSaver, make_connector, no_map_parsing, stored_session, vacuum_cloud  # noqa: F401

TRANSIENT = [bad_gateway, html_page]
TRANSIENT_IDS = ["502-html", "200-html"]


# --- passToken renewal ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", TRANSIENT, ids=TRANSIENT_IDS)
async def test_renewal_outage_at_service_login_is_retryable(answer) -> None:
    cloud = FakeXiaomiCloud()
    cloud.on("GET", SERVICE_LOGIN, answer)
    connector = await XiaomiCloudConnector.from_config(stored_config(), cloud.session_creator)

    with pytest.raises(FailedConnectionException):
        await connector.renew_session()

    assert connector.to_config().service_token == "old-service-token"


async def test_renewal_outage_at_sts_is_retryable() -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login()
    cloud.on("GET", STS, lambda r: FakeResponse(status=503, body="<html>Service Unavailable</html>"))
    connector = await XiaomiCloudConnector.from_config(stored_config(), cloud.session_creator)

    with pytest.raises(FailedConnectionException):
        await connector.renew_session()

    assert connector.to_config().service_token == "old-service-token"


async def test_rejected_token_and_renewal_outage_never_fall_back_to_password_login() -> None:
    cloud = vacuum_cloud()  # the stored token is rejected with 401
    cloud.on("GET", SERVICE_LOGIN, bad_gateway)
    cloud.password_login("captcha")
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    with pytest.raises(FailedConnectionException):
        await connector.get_data()

    assert cloud.password_login_count() == 0


# --- encrypted API during map updates ------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["/v2/homeroom/gethome", "/v2/home/home_device_list"])
@pytest.mark.parametrize("answer", TRANSIENT, ids=TRANSIENT_IDS)
async def test_api_outage_at_startup_is_retryable_not_device_not_found(endpoint, answer) -> None:
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.password_login("captcha")
    cloud.on("POST", f"{API}{endpoint}", answer)
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    with pytest.raises(FailedConnectionException):
        await connector.get_data()

    assert cloud.calls(SERVICE_LOGIN) == []
    assert cloud.password_login_count() == 0


# --- password login ------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", TRANSIENT, ids=TRANSIENT_IDS)
@pytest.mark.parametrize("step", ["serviceLogin", "serviceLoginAuth2", "sts"])
async def test_password_login_outage_is_retryable_not_invalid_credentials(step, answer) -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("ok")
    cloud.sts_issuing("token-from-password")
    cloud.on(*{"serviceLogin": ("GET", SERVICE_LOGIN), "serviceLoginAuth2": ("POST", SERVICE_LOGIN_AUTH2),
               "sts": ("GET", STS)}[step], answer)
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")

    with pytest.raises(FailedConnectionException):
        await connector.login_with_credentials("user@example.com", "secret")


async def test_password_refused_by_xiaomi_is_still_invalid_credentials() -> None:
    """Guard: a JSON refusal keeps meaning "wrong password"."""
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.password_login("invalid")
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")

    with pytest.raises(InvalidCredentialsException):
        await connector.login_with_credentials("user@example.com", "secret")


# --- a failed password login keeps the session it was meant to replace ---------------------------


async def test_failed_password_login_keeps_the_stored_session_and_pass_token() -> None:
    cloud = FakeXiaomiCloud()
    cloud.pass_token_login(accepted=False)
    cloud.on("POST", SERVICE_LOGIN_AUTH2, bad_gateway)
    connector = await XiaomiCloudConnector.from_config(stored_config(), cloud.session_creator)

    with pytest.raises(FailedConnectionException):
        await connector.login_with_credentials("user@example.com", "secret")

    config = connector.to_config()
    assert config.service_token == "old-service-token"
    assert config.user_id is not None
    assert config.pass_token == PASS_TOKEN

    # ...so once Xiaomi accepts the passToken again, the session is renewed without a password.
    cloud.pass_token_login()
    cloud.sts_issuing("renewed-service-token")
    assert await connector.renew_session() is True


async def test_failed_password_login_is_not_repeated_on_every_update() -> None:
    cloud = vacuum_cloud()  # the stored token is rejected with 401
    cloud.pass_token_login(accepted=False)
    cloud.password_login("ok")
    cloud.on("POST", SERVICE_LOGIN_AUTH2, bad_gateway)
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    with pytest.raises(FailedConnectionException):
        await connector.get_data()
    for _ in range(3):  # the coordinator retries, the user presses refresh
        connector.force_refresh()
        with pytest.raises(RetryLaterException):
            await connector.get_data()

    assert cloud.password_login_count() == 1


# --- concurrent updates --------------------------------------------------------------------------


async def test_concurrent_updates_recover_the_session_once() -> None:
    """The refresh button (debounced) and the scheduled update can run at the same time."""
    cloud = vacuum_cloud()  # the stored token is rejected with 401
    cloud.pass_token_login()
    cloud.password_login("captcha")
    cloud.sts_issuing("renewed-service-token")
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    first, second = await asyncio.gather(connector.get_data(), connector.get_data())

    assert first.map_data_raw == second.map_data_raw == b"raw-map-bytes"
    assert len(cloud.calls(SERVICE_LOGIN)) == 1
    assert cloud.password_login_count() == 0
