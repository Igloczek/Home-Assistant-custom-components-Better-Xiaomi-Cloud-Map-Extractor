"""A small in-memory stand-in for the Xiaomi account and Mi Home cloud endpoints.

The connector only uses a narrow slice of ``aiohttp.ClientSession`` (``get``, ``post``,
``cookie_jar`` and ``detach``), so this fake implements exactly that slice. It keeps a real
``aiohttp.CookieJar`` so cookies set by responses (``serviceToken``, ``passToken``, ``userId``)
behave the way they do against the real servers, and it records every request so tests can
assert what was (and was not) sent, e.g. that no password login happened.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any

from aiohttp import ConnectionTimeoutError, CookieJar
from multidict import CIMultiDict
from yarl import URL

from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.utils import decrypt_rc4, encrypt_rc4

ACCOUNT = "https://account.xiaomi.com"
SERVICE_LOGIN = f"{ACCOUNT}/pass/serviceLogin"
SERVICE_LOGIN_AUTH2 = f"{ACCOUNT}/pass/serviceLoginAuth2"
STS = "https://sts.api.io.mi.com/sts"
API = "https://de.api.io.mi.com/app"
QR_LOGIN_URL = f"{ACCOUNT}/longPolling/loginUrl"
QR_LONG_POLL = f"{ACCOUNT}/longPolling/poll"
QR_LINK = f"{ACCOUNT}/longPolling/login"

USER_ID = "1615523263"
C_USER_ID = "cUser-abc"
SSECURITY = base64.b64encode(b"0123456789abcdef").decode()
NEW_SSECURITY = base64.b64encode(b"fedcba9876543210").decode()
PASS_TOKEN = "V1:pass-token-from-login"
THIRTY_DAYS = 30 * 24 * 60 * 60
VERIFY_CONTEXT = "ctx123"
# What a load balancer in front of Xiaomi answers when the backend is down: not JSON, not 200.
BAD_GATEWAY_PAGE = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


@dataclass
class Request:
    method: str
    url: URL
    params: dict[str, str]
    cookies: dict[str, str]
    data: Any

    @property
    def endpoint(self) -> str:
        return str(self.url.with_query(None))


def rc4_request_value(request: Request, name: str) -> str:
    """A request parameter as the client wrote it before RC4-encrypting it (generate_enc_params)."""
    nonce = request.params["_nonce"]
    signed_nonce = base64.b64encode(
        hashlib.sha256(base64.b64decode(request.params["ssecurity"]) + base64.b64decode(nonce)).digest()
    ).decode()
    return decrypt_rc4(signed_nonce, request.params[name]).decode()


@dataclass
class FakeResponse:
    status: int = 200
    body: bytes | str = b""
    set_cookies: dict[str, tuple[str, int | None]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    url: URL | None = None

    def __post_init__(self) -> None:
        self.cookies: SimpleCookie = SimpleCookie()
        for name, (value, max_age) in self.set_cookies.items():
            self.cookies[name] = value
            if max_age is not None:
                self.cookies[name]["max-age"] = str(max_age)
        self.headers = CIMultiDict(self.headers)
        self.content = self

    @property
    def _bytes(self) -> bytes:
        return self.body.encode() if isinstance(self.body, str) else self.body

    async def text(self) -> str:
        return self._bytes.decode(errors="replace")

    async def read(self) -> bytes:
        return self._bytes

    async def json(self) -> Any:
        return json.loads(self._bytes.decode().replace("&&&START&&&", ""))


def json_response(payload: Any, **kwargs: Any) -> FakeResponse:
    return FakeResponse(body="&&&START&&&" + json.dumps(payload), **kwargs)


def bad_gateway(_request: "Request | None" = None) -> FakeResponse:
    return FakeResponse(status=502, body=BAD_GATEWAY_PAGE)


def html_page(_request: "Request | None" = None) -> FakeResponse:
    """A 200 that is not what the API promised, e.g. a captive portal or a maintenance page."""
    return FakeResponse(status=200, body="<html><body>Service maintenance</body></html>")


def connection_timeout(request: "Request") -> FakeResponse:
    """What aiohttp raises when the host does not answer in time: its message quotes the full URL,
    query string included (signatures, `clientSign`, the encrypted API parameters)."""
    raise ConnectionTimeoutError(f"Connection timeout to host {request.url}")


Handler = Callable[[Request], FakeResponse]


class FakeXiaomiCloud:
    """Routes requests to handlers registered per endpoint (URL without query)."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[Request] = []
        self.valid_service_tokens: set[str] = set()
        self.ssecurity = SSECURITY
        # Requests to these endpoints wait until the event is set, like a long poll Xiaomi holds open.
        self.gates: dict[str, asyncio.Event] = {}

    # --- session factory -------------------------------------------------------------
    def session_creator(self) -> "FakeSession":
        return FakeSession(self)

    # --- routing ---------------------------------------------------------------------
    def on(self, method: str, endpoint: str, handler: Handler) -> None:
        self.routes[(method.upper(), endpoint)] = handler

    def calls(self, endpoint: str) -> list[Request]:
        return [r for r in self.requests if r.endpoint == endpoint]

    def dispatch(self, request: Request) -> FakeResponse:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.endpoint))
        if handler is None:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        response = handler(request)
        if response.url is None:
            response.url = request.url
        return response

    # --- canned Xiaomi behaviour ------------------------------------------------------
    def encrypted_api(self, endpoint: str, result: Any | Callable[[Request], Any]) -> None:
        """Register an RC4-encrypted Mi Home API endpoint that checks the serviceToken."""

        def handler(request: Request) -> FakeResponse:
            if request.cookies.get("serviceToken") not in self.valid_service_tokens:
                # Mi Home answers an expired session with 401 and a plain JSON body.
                return FakeResponse(status=401, body='{"code":3,"message":"auth err"}')
            if request.cookies.get("cUserId") != C_USER_ID:
                # Without the cUserId cookie Mi Home answers an error instead of the account's homes (#796).
                return FakeResponse(body='{"code":-8,"message":"user not found"}')
            nonce = request.params["_nonce"]
            signed_nonce = base64.b64encode(
                hashlib.sha256(base64.b64decode(request.params["ssecurity"]) + base64.b64decode(nonce)).digest()
            ).decode()
            payload = result(request) if callable(result) else result
            return FakeResponse(body=encrypt_rc4(signed_nonce, json.dumps(payload)))

        self.on("POST", f"{API}{endpoint}", handler)

    def encrypted_rpc(self, endpoint: str, answer: Callable[[dict[str, Any]], Any]) -> None:
        """An encrypted endpoint whose answer depends on the request's decrypted `data` JSON."""

        def result(request: Request) -> Any:
            return answer(json.loads(rc4_request_value(request, "data")))

        self.encrypted_api(endpoint, result)

    def sts_issuing(self, service_token: str) -> None:
        """The STS callback sets a fresh serviceToken cookie."""

        def handler(request: Request) -> FakeResponse:
            self.valid_service_tokens.add(service_token)
            return FakeResponse(body="ok", set_cookies={"serviceToken": (service_token, None)})

        self.on("GET", STS, handler)

    def pass_token_login(self, *, accepted: bool = True, new_ssecurity: str = NEW_SSECURITY) -> None:
        """serviceLogin answering a passToken cookie with a ready session (code 0) or a fresh sign."""

        def handler(request: Request) -> FakeResponse:
            if accepted and request.cookies.get("passToken") == PASS_TOKEN:
                return json_response(
                    {
                        "code": 0,
                        "userId": int(USER_ID),
                        "cUserId": C_USER_ID,
                        "ssecurity": new_ssecurity,
                        "passToken": PASS_TOKEN,
                        "location": f"{STS}?d=renewed",
                    },
                    set_cookies={"userId": (USER_ID, THIRTY_DAYS)},
                )
            return json_response({"code": 70016, "_sign": "sign-for-password-login", "qs": "%3Fsid"})

        self.on("GET", SERVICE_LOGIN, handler)

    def password_login(self, outcome: str = "ok") -> None:
        """serviceLoginAuth2: 'ok', 'captcha', 'two_factor' or 'invalid'."""

        def handler(request: Request) -> FakeResponse:
            if outcome == "ok":
                return json_response(
                    {
                        "code": 0,
                        "userId": int(USER_ID),
                        "cUserId": C_USER_ID,
                        "ssecurity": self.ssecurity,
                        "passToken": PASS_TOKEN,
                        "location": f"{STS}?d=password",
                    },
                    set_cookies={"userId": (USER_ID, THIRTY_DAYS), "passToken": (PASS_TOKEN, THIRTY_DAYS)},
                )
            if outcome == "captcha":
                return json_response({"code": 87001, "captchaUrl": "/pass/getCode?icodeType=login&0.918"})
            if outcome == "two_factor":
                return json_response(
                    {
                        "code": 0,
                        "notificationUrl": f"{ACCOUNT}/fe/service/identity/authStart?sid=xiaomiio&context=ctx123",
                    }
                )
            return json_response({"code": 70016, "desc": "wrong password"})

        self.on("POST", SERVICE_LOGIN_AUTH2, handler)
        self.on(
            "GET",
            f"{ACCOUNT}/pass/getCode",
            lambda r: FakeResponse(body=b"\xff\xd8\xff\xe0JFIF-captcha-image-bytes"),
        )
        self.on("GET", f"{ACCOUNT}/fe/service/identity/authStart", lambda r: FakeResponse(body="<html/>"))
        self.on("GET", f"{ACCOUNT}/identity/list", lambda r: FakeResponse(body="{}"))
        self.on(
            "POST", f"{ACCOUNT}/identity/auth/sendEmailTicket", lambda r: json_response({"code": 0})
        )

    def email_verification(self, service_token: str) -> None:
        """The identity flow after the user typed the emailed code: verifyEmail, result/check, Auth2/end, STS."""
        end = f"{ACCOUNT}/pass/serviceLoginAuth2/end"
        self.on(
            "POST",
            f"{ACCOUNT}/identity/auth/verifyEmail",
            lambda r: json_response(
                {"code": 0, "location": f"{ACCOUNT}/identity/result/check?sid=xiaomiio&context={VERIFY_CONTEXT}"}
            ),
        )
        self.on(
            "GET",
            f"{ACCOUNT}/identity/result/check",
            lambda r: FakeResponse(status=302, headers={"Location": f"{end}?context={VERIFY_CONTEXT}&t=end-ticket"}),
        )
        self.on(
            "GET",
            end,
            lambda r: FakeResponse(
                status=302,
                headers={
                    "extension-pragma": json.dumps({"ssecurity": self.ssecurity, "psecurity": "psecurity-value"}),
                    "Location": f"{STS}?d=verified&clientSign=client-sign-value",
                },
            ),
        )

        def sts(request: Request) -> FakeResponse:
            self.valid_service_tokens.add(service_token)
            return FakeResponse(
                body="ok",
                set_cookies={"serviceToken": (service_token, None), "userId": (USER_ID, THIRTY_DAYS)},
            )

        self.on("GET", STS, sts)

    def qr_login(self, service_token: str, *, timeout: int = 300) -> None:
        """serviceLogin without a session, the QR code, and the long poll the app completes.

        Like the real cloud, the long poll answers with the session but no cUserId cookie; the cUserId
        only comes in its JSON. ``loginUrl`` redirects to serviceLogin with ``_json=true``, which a
        signed-out browser gets as a JSON document instead of the sign-in page (#766).
        """
        self.on(
            "GET",
            SERVICE_LOGIN,
            lambda r: json_response(
                {
                    "code": 70016,
                    "_sign": "sign-for-qr",
                    "qs": "%3Fsid%3Dxiaomiio",
                    "callback": STS,
                    "location": f"{ACCOUNT}/fe/service/login?serviceParam=service-param",
                }
            ),
        )
        self.on(
            "GET",
            QR_LOGIN_URL,
            lambda r: json_response(
                {
                    "code": 0,
                    "qr": f"{ACCOUNT}/qr.png",
                    "lp": f"{QR_LONG_POLL}?ticket=qr-poll-ticket",
                    "loginUrl": f"{QR_LINK}?ticket=qr-login-ticket",
                    "timeout": timeout,
                }
            ),
        )
        self.on("GET", f"{ACCOUNT}/qr.png", lambda r: FakeResponse(body=b"\x89PNG-qr-image"))
        self.on(
            "GET",
            QR_LINK,
            lambda r: FakeResponse(
                status=302,
                headers={
                    "Location": str(
                        URL(SERVICE_LOGIN).with_query(
                            {
                                "sid": "xiaomiio",
                                "_json": "true",
                                "callback": f"{STS}?d=qr",
                                "followup": f"{ACCOUNT}/longPolling/followup?ticket=qr-login-ticket",
                            }
                        )
                    )
                },
            ),
        )
        self.qr_scanned()
        self.sts_issuing(service_token)

    def qr_scanned(self) -> None:
        """The long poll answers with the session once the app approved the login."""
        self.on(
            "GET",
            QR_LONG_POLL,
            lambda r: json_response(
                {
                    "code": 0,
                    "userId": int(USER_ID),
                    "cUserId": C_USER_ID,
                    "ssecurity": self.ssecurity,
                    "passToken": PASS_TOKEN,
                    "location": f"{STS}?d=qr&clientSign=client-sign-value",
                },
                set_cookies={"userId": (USER_ID, THIRTY_DAYS), "passToken": (PASS_TOKEN, THIRTY_DAYS)},
            ),
        )

    def qr_long_poll(self, handler: Handler) -> None:
        self.on("GET", QR_LONG_POLL, handler)

    def password_login_count(self) -> int:
        return len(self.calls(SERVICE_LOGIN_AUTH2))


class FakeSession:
    def __init__(self, cloud: FakeXiaomiCloud) -> None:
        self._cloud = cloud
        self.cookie_jar = CookieJar()

    def detach(self) -> None:
        pass

    async def get(self, url, **kwargs) -> FakeResponse:
        await asyncio.sleep(0)  # a real request yields to the event loop, so concurrent updates interleave
        if (gate := self._cloud.gates.get(str(URL(str(url)).with_query(None)))) is not None:
            await gate.wait()
        return self._request("GET", url, **kwargs)

    async def post(self, url, **kwargs) -> FakeResponse:
        await asyncio.sleep(0)
        return self._request("POST", url, **kwargs)

    def _request(self, method, url, *, params=None, cookies=None, data=None, **_kwargs) -> FakeResponse:
        url = URL(str(url))
        if params:
            url = url.update_query({k: str(v) for k, v in params.items()})
        sent_cookies = {k: m.value for k, m in self.cookie_jar.filter_cookies(url).items()}
        sent_cookies.update({k: str(v) for k, v in (cookies or {}).items()})
        request = Request(method, url, dict(url.query), sent_cookies, data)
        response = self._cloud.dispatch(request)
        self.cookie_jar.update_cookies(response.cookies, url)
        return response
