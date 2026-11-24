"""Users paste debug logs into public issues: no session secret may ever be logged (#778).

Every login path and the map download run at DEBUG level against the fake cloud, then the whole log
is searched for each secret the fake handed out.
"""

from __future__ import annotations

import datetime
import hashlib
import logging

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    CaptchaRequiredException,
    FailedLoginException,
    TwoFactorAuthRequiredException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

from .fake_xiaomi import (
    ACCOUNT,
    NEW_SSECURITY,
    PASS_TOKEN,
    SSECURITY,
    VERIFY_CONTEXT,
    FakeResponse,
    FakeXiaomiCloud,
)
from .test_cloud_session import stored_config
from .test_session_lifecycle import (  # noqa: F401
    MAP_URL,
    MAP_URL_SIGNATURE,
    SessionSaver,
    make_connector,
    no_map_parsing,
    stored_session,
    vacuum_cloud,
)

PASSWORD = "secret"
SECRETS = {
    "password": PASSWORD,
    "password hash": hashlib.md5(PASSWORD.encode()).hexdigest().upper(),
    "passToken": PASS_TOKEN,
    "ssecurity": SSECURITY,
    "renewed ssecurity": NEW_SSECURITY,
    "psecurity": "psecurity-value",
    "serviceToken": "-service-token",  # every token the fake issues ends like this
    "STS clientSign": "client-sign-value",
    "verification context": VERIFY_CONTEXT,
    "verification ticket": "end-ticket",
    "QR long-poll ticket": "qr-poll-ticket",
    "QR login ticket": "qr-login-ticket",
    "signed map URL": MAP_URL_SIGNATURE,
    "captcha image": "JFIF",
}


@pytest.fixture
def debug_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def assert_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    records = caplog.records
    assert records, "the scenario logged nothing, so it proves nothing"
    log = "\n".join(f"{record.getMessage()} {record.exc_text or ''}" for record in records)
    leaked = {name for name, secret in SECRETS.items() if secret in log}
    assert not leaked, f"secrets in the debug log: {sorted(leaked)}"


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


async def test_failed_qr_long_poll(debug_log) -> None:
    cloud = FakeXiaomiCloud()
    cloud.qr_login("qr-service-token")
    cloud.on("GET", f"{ACCOUNT}/longPolling/poll",
             lambda r: FakeResponse(status=500, body=f"error for {r.url}"))
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    await connector.login_with_qr_get_code()

    with pytest.raises(FailedLoginException):
        await connector.login_with_qr_wait_for_completion()
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

    assert await connector.continue_login_with_two_factor("123456", verification.value) is False
    assert_no_secrets(debug_log)


async def test_refused_map_download(debug_log) -> None:
    """S3-style storage answers a bad signature with a page that quotes it."""
    cloud = vacuum_cloud()
    cloud.valid_service_tokens.add("stored-service-token")
    cloud.on("GET", MAP_URL, lambda r: FakeResponse(status=403, body=f"SignatureDoesNotMatch {r.url}"))
    connector = make_connector(cloud, stored_session(expires_in=datetime.timedelta(days=20)), SessionSaver())

    await connector.get_data()
    assert_no_secrets(debug_log)
