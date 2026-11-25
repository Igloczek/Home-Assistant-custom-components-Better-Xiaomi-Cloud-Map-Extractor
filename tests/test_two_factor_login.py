"""Identity verification (two-factor by email) at the connector, against the shapes Xiaomi answers in.

After the code is typed in, Xiaomi's answer varies: verifyEmail with or without JSON, result/check with
or without a redirect, Auth2/end with a "Tips" page first or the STS link only in its body. Each shape
either completes the login or ends it with False and the previous session untouched.
"""

from __future__ import annotations

import json

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    TwoFactorAuthRequiredException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

from .fake_xiaomi import ACCOUNT, STS, VERIFY_CONTEXT, FakeResponse, FakeXiaomiCloud, json_response

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


def _verify_email_page_with_check_link(_request) -> FakeResponse:
    return FakeResponse(body=f'<html><a href="{CHECK_LINK}">continue</a></html>')


def _verify_email_redirect(_request) -> FakeResponse:
    return FakeResponse(status=200, body="", headers={"Location": CHECK_LINK})


def _verify_email_without_location(_request) -> FakeResponse:
    return json_response({"code": 0})


SUCCEEDING = {
    "verifyEmail redirects instead of answering JSON": (VERIFY_EMAIL, "POST", _verify_email_redirect),
    "verifyEmail answers a page linking result/check": (VERIFY_EMAIL, "POST", _verify_email_page_with_check_link),
    "verifyEmail answers JSON without a location": (VERIFY_EMAIL, "POST", _verify_email_without_location),
}


@pytest.mark.parametrize(("endpoint", "method", "answer"), SUCCEEDING.values(), ids=SUCCEEDING.keys())
async def test_verification_finishes_whatever_verify_email_answers(endpoint, method, answer) -> None:
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)
    cloud.on(method, endpoint, answer)

    assert await connector.continue_login_with_two_factor("123456", verification) is True
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


async def test_sts_link_only_in_the_auth2_end_page() -> None:
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)
    pragma = json.dumps({"ssecurity": cloud.ssecurity})
    cloud.on(
        "GET",
        AUTH2_END,
        lambda r: FakeResponse(status=200, body=f'<script>go("{STS_LINK}")</script>', headers={"extension-pragma": pragma}),
    )

    assert await connector.continue_login_with_two_factor("123456", verification) is True
    assert connector.has_service_token()


def _no_location(_request) -> FakeResponse:
    return FakeResponse(status=200, body="")


FAILING = {
    "code refused": (VERIFY_EMAIL, "POST", lambda r: FakeResponse(status=403, body="")),
    "no finish location anywhere": (RESULT_CHECK, "GET", _no_location),
    "Auth2/end without ssecurity": (AUTH2_END, "GET", lambda r: FakeResponse(status=302, headers={"Location": STS_LINK})),
    "Auth2/end without STS link": (
        AUTH2_END,
        "GET",
        lambda r: FakeResponse(status=200, body="<html/>", headers={"extension-pragma": json.dumps({"ssecurity": "c2Vj"})}),
    ),
    "STS fails": (STS, "GET", lambda r: FakeResponse(status=500, body="")),
    "STS issues no serviceToken": (STS, "GET", lambda r: FakeResponse(status=200, body="ok")),
}


@pytest.mark.parametrize(("endpoint", "method", "answer"), FAILING.values(), ids=FAILING.keys())
async def test_unfinished_verification_returns_false_and_keeps_no_session(endpoint, method, answer) -> None:
    cloud = FakeXiaomiCloud()
    connector, verification = await _verification_asked(cloud)
    cloud.on(method, endpoint, answer)
    if endpoint == RESULT_CHECK:
        # Without a location in verifyEmail's answer, result/check is asked directly.
        cloud.on("POST", VERIFY_EMAIL, _verify_email_without_location)

    assert await connector.continue_login_with_two_factor("123456", verification) is False
    assert not connector.has_service_token()
