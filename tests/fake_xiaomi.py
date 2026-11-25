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

from aiohttp import ClientTimeout, ConnectionTimeoutError, CookieJar
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
IDENTITY_SESSION = "identity-session-value"
# The code the fake account mails (or texts) for identity verification.
VERIFICATION_CODE = "123456"
CAPTCHA_SOLUTION = "Ab3d"
CAPTCHA_ICK = "captcha-ick-value"
FLAG_PHONE = 4
FLAG_EMAIL = 8
# Where verifyEmail/verifyPhone sends the browser once the code is accepted. Xiaomi's answer varies
# (account host, region, first hop); every variant ends at Auth2/end and then STS (#780).
VERIFY_FINISH = {
    "result_check": f"{ACCOUNT}/identity/result/check?sid=xiaomiio&context={VERIFY_CONTEXT}&_locale=en_US",
    "regional_result_check":
        f"https://ru.account.xiaomi.com/identity/result/check?sid=xiaomiio&context={VERIFY_CONTEXT}&_locale=en_US",
    "auth2_end": f"{ACCOUNT}/pass/serviceLoginAuth2/end?context={VERIFY_CONTEXT}&t=end-ticket",
    "tips_page_first": f"{ACCOUNT}/identity/result/check?sid=xiaomiio&context={VERIFY_CONTEXT}&_locale=en_US",
}
# What a load balancer in front of Xiaomi answers when the backend is down: not JSON, not 200.
BAD_GATEWAY_PAGE = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


@dataclass
class Request:
    method: str
    url: URL
    params: dict[str, str]
    cookies: dict[str, str]
    data: Any
    timeout: ClientTimeout | None = None

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
        self.history: tuple[FakeResponse, ...] = ()

    @property
    def _bytes(self) -> bytes:
        return self.body.encode() if isinstance(self.body, str) else self.body

    async def text(self) -> str:
        return self._bytes.decode(errors="replace")

    async def read(self) -> bytes:
        return self._bytes

    async def json(self) -> Any:
        """Like aiohttp: no `&&&START&&&` stripping. Xiaomi's passport prefixes every JSON body with it,
        so `response.json()` never works on a real passport answer (#780)."""
        return json.loads(self._bytes.decode())


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
        # Hosts that accept the connection and never answer (a sanctions or DPI block, #700). Like aiohttp,
        # the request ends only when its timeout runs out; without one, aiohttp's default is 5 minutes,
        # which the fake treats as forever.
        self.hanging_hosts: set[str] = set()

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
        self.captcha_image()
        self.identity_verification()

    def captcha_image(self) -> None:
        """The captcha image; its response sets the `ick` cookie the answer is checked against."""
        self.on(
            "GET",
            f"{ACCOUNT}/pass/getCode",
            lambda r: FakeResponse(
                body=b"\xff\xd8\xff\xe0JFIF-captcha-image-bytes", set_cookies={"ick": (CAPTCHA_ICK, None)}
            ),
        )

    def captcha_account(self, password: str, *, captcha: str = CAPTCHA_SOLUTION) -> None:
        """serviceLoginAuth2 of an account Xiaomi now guards with a captcha (#712).

        Without a captcha, or with a wrong one, it answers 87001 and a new captchaUrl. With the right
        captcha and a wrong password it answers 70016 and keeps asking for a captcha next time, as
        XiaomiGateway3 and hass-xiaomi-miot handle it; with both right it logs in.
        """
        password_hash = hashlib.md5(password.encode()).hexdigest().upper()
        captcha_url = "/pass/getCode?icodeType=login&0.918"

        def handler(request: Request) -> FakeResponse:
            if request.params.get("captCode") != captcha or request.cookies.get("ick") != CAPTCHA_ICK:
                return json_response({"code": 87001, "desc": "captcha", "captchaUrl": captcha_url})
            if request.params.get("hash") != password_hash:
                return json_response({"code": 70016, "desc": "wrong password", "captchaUrl": captcha_url})
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

        self.on("POST", SERVICE_LOGIN_AUTH2, handler)
        self.captcha_image()

    def identity_verification(self, *, flag: int = FLAG_EMAIL, send_code: int = 0) -> None:
        """authStart, identity/list and sending the code, as XiaomiGateway3 and hass-xiaomi-miot see them.

        identity/list answers code 2 with the method (`flag` 4 phone, 8 email) and sets the
        `identity_session` cookie every later identity request needs. `send_code` is what
        send{Phone,Email}Ticket answers: 0 sent, 87001 (with a captchaUrl) or anything else not sent.
        """
        channel = "Phone" if flag == FLAG_PHONE else "Email"
        self.on("GET", f"{ACCOUNT}/fe/service/identity/authStart", lambda r: FakeResponse(body="<html/>"))
        self.on(
            "GET",
            f"{ACCOUNT}/identity/list",
            lambda r: json_response(
                {"code": 2, "flag": flag, "options": [flag]},
                set_cookies={"identity_session": (IDENTITY_SESSION, None)},
            ),
        )

        def needs_session(answer: Callable[[Request], FakeResponse]) -> Handler:
            def handler(request: Request) -> FakeResponse:
                if request.cookies.get("identity_session") != IDENTITY_SESSION:
                    return json_response({"code": 70016, "desc": "identity session missing"})
                return answer(request)

            return handler

        self.on(
            "GET",
            f"{ACCOUNT}/identity/auth/verify{channel}",
            needs_session(lambda r: json_response({"code": 0, f"masked{channel}": "u***@example.com"})),
        )

        def send(request: Request) -> FakeResponse:
            if send_code == 87001:
                return json_response({"code": 87001, "captchaUrl": "/pass/getCode?icodeType=antispam"})
            return json_response({"code": send_code})

        self.on("POST", f"{ACCOUNT}/identity/auth/send{channel}Ticket", needs_session(send))

    def email_verification(
        self, service_token: str, *, finish: str = "result_check", flag: int = FLAG_EMAIL
    ) -> None:
        """Checking the code and finishing the login: verify{Email,Phone}, its `location`, Auth2/end, STS.

        verify answers `&&&START&&&{"code": 0, "location": ...}` for the right code and a non-zero code
        for a wrong one. Following the location ends at Auth2/end, which carries `ssecurity` in its
        `extension-pragma` header and sets the passToken, then at STS, which sets the serviceToken
        (XiaomiGateway3 `_get_credentials`). `finish` picks the location variant (VERIFY_FINISH).
        """
        channel = "Phone" if flag == FLAG_PHONE else "Email"
        location = VERIFY_FINISH[finish]
        host = str(URL(location).origin())
        end = f"{host}/pass/serviceLoginAuth2/end"
        tips_shown: list[bool] = []
        verified: list[bool] = []
        not_verified = FakeResponse(body="<html><title>Xiaomi Account</title>Verify your identity</html>")

        def verify(request: Request) -> FakeResponse:
            data = request.data or {}
            if request.cookies.get("identity_session") != IDENTITY_SESSION:
                return json_response({"code": 70016, "desc": "identity session missing"})
            if data.get("ticket") != VERIFICATION_CODE:
                return json_response({"code": 70014, "tips": "wrong verification code"})
            verified.append(True)
            return json_response({"code": 0, "location": location})

        def result_check(request: Request) -> FakeResponse:
            # Only a verified identity session moves on; otherwise the page asks to verify again.
            if not verified:
                return not_verified
            return FakeResponse(status=302, headers={"Location": f"{end}?context={VERIFY_CONTEXT}&t=end-ticket"})

        def auth2_end(request: Request) -> FakeResponse:
            if not verified:
                return not_verified
            if finish == "tips_page_first" and not tips_shown:
                tips_shown.append(True)
                return FakeResponse(body="<html><title>Xiaomi Account - Tips</title></html>")
            return FakeResponse(
                status=302,
                headers={
                    "extension-pragma": json.dumps({"ssecurity": self.ssecurity, "psecurity": "psecurity-value"}),
                    "Location": f"{STS}?d=verified&clientSign=client-sign-value",
                },
                set_cookies={"passToken": (PASS_TOKEN, THIRTY_DAYS), "userId": (USER_ID, THIRTY_DAYS)},
            )

        def sts(request: Request) -> FakeResponse:
            self.valid_service_tokens.add(service_token)
            return FakeResponse(
                body="ok",
                set_cookies={
                    "serviceToken": (service_token, None),
                    "userId": (USER_ID, THIRTY_DAYS),
                    "cUserId": (C_USER_ID, None),
                },
            )

        self.on("POST", f"{ACCOUNT}/identity/auth/verify{channel}", verify)
        self.on("GET", f"{host}/identity/result/check", result_check)
        if host != ACCOUNT:
            # The identity session lives on the regional host; the global one does not know it.
            self.on("GET", f"{ACCOUNT}/identity/result/check", lambda r: not_verified)
        self.on("GET", end, auth2_end)
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


REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class FakeSession:
    def __init__(self, cloud: FakeXiaomiCloud) -> None:
        self._cloud = cloud
        self.cookie_jar = CookieJar()

    def detach(self) -> None:
        pass

    async def get(self, url, **kwargs) -> FakeResponse:
        return await self._send("GET", url, **kwargs)

    async def post(self, url, **kwargs) -> FakeResponse:
        return await self._send("POST", url, **kwargs)

    async def _send(self, method, url, *, allow_redirects: bool = True, **kwargs) -> FakeResponse:
        timeout = kwargs.pop("timeout", None)
        await asyncio.sleep(0)  # a real request yields to the event loop, so concurrent updates interleave
        if URL(str(url)).host in self._cloud.hanging_hosts:
            self._cloud.requests.append(Request(method, URL(str(url)), {}, {}, kwargs.get("data"), timeout))
            if timeout is None:
                await asyncio.Event().wait()  # aiohttp's default timeout: 5 minutes, i.e. forever here
            raise TimeoutError()
        if (gate := self._cloud.gates.get(str(URL(str(url)).with_query(None)))) is not None:
            await gate.wait()
        response = self._request(method, url, timeout=timeout, **kwargs)
        history: list[FakeResponse] = []
        # Like aiohttp: redirects are followed unless the caller asks not to, and kept in `history`.
        while allow_redirects and response.status in REDIRECT_STATUSES and response.headers.get("Location"):
            history.append(response)
            if len(history) > 10:
                raise AssertionError(f"Redirect loop at {response.url}")
            response = self._request("GET", response.url.join(URL(response.headers["Location"])), timeout=timeout)
        response.history = tuple(history)
        return response

    def _request(self, method, url, *, params=None, cookies=None, data=None, timeout=None, **_kwargs) -> FakeResponse:
        url = URL(str(url))
        if params:
            url = url.update_query({k: str(v) for k, v in params.items()})
        sent_cookies = {k: m.value for k, m in self.cookie_jar.filter_cookies(url).items()}
        sent_cookies.update({k: str(v) for k, v in (cookies or {}).items()})
        request = Request(method, url, dict(url.query), sent_cookies, data, timeout)
        response = self._cloud.dispatch(request)
        self.cookie_jar.update_cookies(response.cookies, url)
        return response
