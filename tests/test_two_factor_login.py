"""Identity verification (two-factor by email) at the connector, against the shapes Xiaomi answers in.

After the code is typed in, Xiaomi's answer varies: verifyEmail with or without passport JSON, with or
without a location, Auth2/end with a "Tips" page first or no STS redirect at all. Each shape either
completes the login or raises the error the form explains (refused code, accepted code without a
session, Xiaomi unavailable), with the previous session untouched.
"""

from __future__ import annotations

import json

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    InvalidTwoFactorCodeException,
    TwoFactorAuthRequiredException,
    VerificationIncompleteException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

from .fake_xiaomi import ACCOUNT, STS, VERIFICATION_CODE, VERIFY_CONTEXT, FakeResponse, FakeXiaomiCloud, json_response

VERIFY_EMAIL = f"{ACCOUNT}/identity/auth/verifyEmail"
RESULT_CHECK = f"{ACCOUNT}/identity/result/check"
AUTH2_END = f"{ACCOUNT}/pass/serviceLoginAuth2/end"
CHECK_LINK = f"{RESULT_CHECK}?sid=xiaomiio&context={VERIFY_CONTEXT}"
END_LINK = f"{AUTH2_END}?context={VERIFY_CONTEXT}&t=end-ticket"
STS_LINK = f"{STS}?d=verified&clientSign=client-sign-value"
SERVICE_TOKEN = "verified-service-token"


async def _verification_asked(cloud: FakeXiaomiCloud) -> tuple[XiaomiCloudConnector, TwoFactorAuthRequiredException]:
    cloud.pass_token_login(accepted=False)
    cloud.password_login("two_factor")
    cloud.email_verification(SERVICE_TOKEN)
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    with pytest.raises(TwoFactorAuthRequiredException) as verification:
        await connector.login_with_credentials("user@example.com", "made-up-password")
    return connector, verification.value


def _end_page(cloud: FakeXiaomiCloud, **headers: str) -> FakeResponse:
    pragma = json.dumps({"ssecurity": cloud.ssecurity, "psecurity": "psecurity-value"})
    return FakeResponse(status=302, headers={"extension-pragma": pragma, "Location": STS_LINK, **headers})


async def test_verification_code_finishes_the_login() -> None:
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)

    assert await connector.continue_login_with_two_factor(VERIFICATION_CODE, verification) is True
    assert connector.has_service_token()
    assert connector.to_config().service_token == SERVICE_TOKEN


async def test_tips_page_before_auth2_end_is_skipped() -> None:
    """Some servers answer Auth2/end with an HTML "Tips" page first and redirect on the next call."""
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)
    answers = iter([FakeResponse(status=200, body="<title>Xiaomi Account - Tips</title>"), _end_page(cloud)])
    cloud.on("GET", AUTH2_END, lambda r: next(answers))

    assert await connector.continue_login_with_two_factor("123456", verification) is True
    assert len(cloud.calls(AUTH2_END)) == 2


def _no_location(_request) -> FakeResponse:
    return FakeResponse(status=200, body="")


def _verify_email_redirect(_request) -> FakeResponse:
    return FakeResponse(status=200, body="", headers={"Location": CHECK_LINK})


def _verify_email_page_with_check_link(_request) -> FakeResponse:
    return FakeResponse(body=f'<html><a href="{CHECK_LINK}">continue</a></html>')


def _verify_email_without_location(_request) -> FakeResponse:
    return json_response({"code": 0})


def _sts_link_only_in_the_auth2_end_page(_request) -> FakeResponse:
    pragma = json.dumps({"ssecurity": "c2Vj"})
    return FakeResponse(status=200, body=f'<script>go("{STS_LINK}")</script>', headers={"extension-pragma": pragma})


# verify's passport JSON and its `location` are the only way on: the shapes without them used to be
# scraped for a result/check or STS link; they now end with an error the form can explain (fork PR #26).
FAILING = {
    "code refused": (VERIFY_EMAIL, "POST", lambda r: json_response({"code": 70014}), InvalidTwoFactorCodeException),
    "verifyEmail refuses without JSON": (
        VERIFY_EMAIL, "POST", lambda r: FakeResponse(status=403, body=""), FailedConnectionException
    ),
    "verifyEmail redirects instead of answering JSON": (
        VERIFY_EMAIL, "POST", _verify_email_redirect, FailedConnectionException
    ),
    "verifyEmail answers a page linking result/check": (
        VERIFY_EMAIL, "POST", _verify_email_page_with_check_link, FailedConnectionException
    ),
    "verifyEmail answers JSON without a location": (
        VERIFY_EMAIL, "POST", _verify_email_without_location, VerificationIncompleteException
    ),
    "result/check does not redirect": (RESULT_CHECK, "GET", _no_location, VerificationIncompleteException),
    "Auth2/end without ssecurity": (
        AUTH2_END,
        "GET",
        lambda r: FakeResponse(status=302, headers={"Location": STS_LINK}),
        VerificationIncompleteException,
    ),
    "Auth2/end without STS redirect": (
        AUTH2_END, "GET", _sts_link_only_in_the_auth2_end_page, VerificationIncompleteException
    ),
    "STS fails": (STS, "GET", lambda r: FakeResponse(status=500, body=""), FailedConnectionException),
    "STS issues no serviceToken": (
        STS, "GET", lambda r: FakeResponse(status=200, body="ok"), VerificationIncompleteException
    ),
}


@pytest.mark.parametrize(("endpoint", "method", "answer", "raised"), FAILING.values(), ids=FAILING.keys())
async def test_unfinished_verification_raises_and_keeps_no_session(endpoint, method, answer, raised) -> None:
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)
    cloud.on(method, endpoint, answer)

    with pytest.raises(raised):
        await connector.continue_login_with_two_factor("123456", verification)
    assert not connector.has_service_token()
