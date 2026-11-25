from __future__ import annotations
import asyncio
import base64
import datetime
import hashlib
import json
import locale
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from typing import Optional, Self, Unpack, Any
from urllib.parse import parse_qs, urlparse

import tzlocal
from aiohttp import ClientError, ClientSession, ClientResponse
from aiohttp.client import _RequestOptions, ClientTimeout
from aiohttp.typedefs import StrOrURL
from yarl import URL

from .const import AVAILABLE_SERVERS, SERVER_CN
from .utils import generate_agent, generate_device_id, to_json, generate_nonce, generate_enc_params, decrypt_rc4
from ..utils.exceptions import (
    TwoFactorAuthRequiredException,
    InvalidCredentialsException,
    FailedLoginException,
    FailedConnectionException,
    CaptchaRequiredException,
    SessionExpiredException,
    UnexpectedResponseError,
    QrLoginTimeoutException,
    InvalidTwoFactorCodeException,
    VerificationIncompleteException,
)
from ..utils.dict_operations import path_extractor

_LOGGER = logging.getLogger(__name__)

# Errors that mean "Xiaomi could not be reached", as opposed to "Xiaomi said no".
# They must never lead to a password login: that is what makes Xiaomi ask for a captcha.
NETWORK_ERRORS = (ClientError, asyncio.TimeoutError, TimeoutError, OSError)

# serviceLogin is asked to renew the session this long before the stored expiration.
SESSION_RENEWAL_MARGIN = datetime.timedelta(days=1)
DEFAULT_SESSION_LIFETIME = datetime.timedelta(days=20)

# A QR code is valid for the "timeout" Xiaomi sends with it; these apply when it is missing or absurd.
QR_CODE_LIFETIME = datetime.timedelta(minutes=5)
QR_CODE_LIFETIME_MAX = datetime.timedelta(minutes=15)
# Each long poll is held open by Xiaomi; it is asked again when it ends before the code expires.
QR_POLL_TIMEOUT = 60
QR_REQUEST_TIMEOUT = ClientTimeout(total=15)
# Every other request to Xiaomi. aiohttp's default waits 5 minutes for a host that accepts the connection
# and never answers, which a blocked region does (#700): setup then hung for minutes after the code.
REQUEST_TIMEOUT = ClientTimeout(total=20, sock_connect=10)

ACCOUNT_URL = "https://account.xiaomi.com"
# Identity verification methods, as identity/list names them.
FLAG_PHONE = 4
FLAG_EMAIL = 8
# Redirects between an accepted verification code and STS (result/check, Auth2/end, STS, a tips page).
MAX_LOGIN_HOPS = 10
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
PASSPORT_PREFIX = "&&&START&&&"


def _passport_json(text: str) -> dict[str, Any] | None:
    """A Xiaomi passport answer: JSON behind `&&&START&&&`, which `response.json()` can't read."""
    if text.startswith(PASSPORT_PREFIX):
        text = text[len(PASSPORT_PREFIX):]
    try:
        result = json.loads(text)
    except ValueError:
        return None
    return result if isinstance(result, dict) else None


def _is_outage(status: int) -> bool:
    """Statuses that mean Xiaomi, or a proxy in front of it, is down or busy: never a refusal."""
    return status >= 500 or status in (408, 429)


def _unavailable(status: int) -> FailedConnectionException:
    """Xiaomi answered, but not with its API: retry later like after a timeout, never log in again."""
    return FailedConnectionException(UnexpectedResponseError(status))


@dataclass
class XiaomiCloudConnectorConfig:
    username: str | None
    password: str | None
    server: str
    user_id: str
    c_user_id: str
    service_token: str
    expiration: datetime.datetime
    ssecurity: str
    device_id: str
    pass_token: str | None = None

    @classmethod
    def from_dict(
        cls, data: dict[str, str] | XiaomiCloudConnectorConfig
    ) -> XiaomiCloudConnectorConfig:
        _LOGGER.debug("Restoring connector config...")
        if isinstance(data, XiaomiCloudConnectorConfig):
            return data
        expiration = data["expiration"]
        if isinstance(expiration, str):
            expiration = datetime.datetime.fromisoformat(expiration)
        if isinstance(expiration, datetime.datetime) and expiration.tzinfo is not None:
            # Sessions are compared with naive local time; a store written with a UTC offset must still load.
            expiration = expiration.astimezone().replace(tzinfo=None)
        known = {f.name for f in fields(cls)}
        return cls(**{**{k: v for k, v in data.items() if k in known}, "expiration": expiration})


@dataclass
class XiaomiCloudHome:
    home_id: int
    owner: int


@dataclass
class XiaomiCloudDeviceInfo:
    device_id: str
    name: str
    model: str
    token: str
    spec_type: str
    local_ip: str | None
    mac: str | None
    server: str
    home_id: int
    user_id: int


@dataclass
class XiaomiCloudSessionData:
    session: ClientSession
    headers: dict[str, str]
    ssecurity: str | None = None
    userId: str | None = None
    cUserId: str | None = None
    serviceToken: str | None = None
    expiration: datetime.datetime | None = None

    def __repr__(self) -> str:
        # Never render the serviceToken or ssecurity, this object ends up in debug logs.
        return (f"XiaomiCloudSessionData(userId={self.userId}, has_service_token={self.serviceToken is not None}, "
                f"expiration={self.expiration})")

    def has_service_token(self) -> bool:
        return self.serviceToken is not None and self.ssecurity is not None

    def is_expiring(self) -> bool:
        return self.expiration is None or self.expiration < datetime.datetime.now() + SESSION_RENEWAL_MARGIN

    def is_authenticated(self) -> bool:
        return self.has_service_token() and not self.is_expiring()

    async def get(self: Self, url: StrOrURL, **kwargs: Unpack[_RequestOptions]):
        passed_headers = kwargs.pop("headers", {})
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return await self.session.get(url, headers={**passed_headers, **self.headers}, **kwargs)

    async def post(self: Self, url: StrOrURL, **kwargs: Unpack[_RequestOptions]) -> ClientResponse:
        passed_headers = kwargs.pop("headers", {})
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return await self.session.post(url, headers={**passed_headers, **self.headers}, **kwargs)

    def apply_from_parameters(self):
        cookie_jar = self.session.cookie_jar
        domains = [
            "https://account.xiaomi.com",
            "https://api.io.mi.com",
            "https://sts.api.io.mi.com",
        ]
        cookies = {
            k: str(v)
            for k, v in {
                "serviceToken": self.serviceToken,
                "yetAnotherServiceToken": self.serviceToken,
                "userId": self.userId,
                "cUserId": self.cUserId,
            }.items()
            if v is not None
        }
        for domain in domains:
            cookie_jar.update_cookies(cookies, URL(domain))


# noinspection PyBroadException
class XiaomiCloudConnector:
    _username: str | None
    _password: str | None
    _long_polling_url: str | None
    _session_data: XiaomiCloudSessionData | None
    _locale: str
    _timezone: str
    _sign: str | None
    server: str | None

    def __init__(self: Self, session_creator: Callable[[], ClientSession], server: str | None = None):
        self._username = None
        self._password = None
        self._session_creator = session_creator
        self._locale = locale.getdefaultlocale()[0] or "en_GB"
        timezone = datetime.datetime.now(tzlocal.get_localzone()).strftime('%z')
        self._timezone = f"GMT{timezone[:-2]}:{timezone[-2:]}"
        self.server = server
        self._session_data = None
        self._long_polling_url = None
        self._qr_deadline = 0.0
        self.device_id = generate_device_id()
        self.pass_token: str | None = None
        # A captcha or identity verification continues on the session the login started on.
        self._pending_login: XiaomiCloudSessionData | None = None

    async def create_session(self: Self) -> None:
        if self._session_data is not None and self._session_data.session is not None:
            self._session_data.session.detach()
        self._session_data = self._new_session_data()

    def _new_session_data(self: Self) -> XiaomiCloudSessionData:
        agent = generate_agent()
        session = self._session_creator()
        cookies = {
            "sdkVerdion": "accountsdk-18.8.15",
            "deviceId": self.device_id
        }
        session.cookie_jar.update_cookies(cookies, response_url=URL("mi.com"))
        session.cookie_jar.update_cookies(cookies, response_url=URL("xiaomi.com"))
        headers = {"User-Agent": agent, "Content-Type": "application/x-www-form-urlencoded"}
        return XiaomiCloudSessionData(session, headers)

    @asynccontextmanager
    async def _login_attempt(self: Self, *, fresh: bool) -> AsyncIterator[None]:
        """Runs a login on its own session; the current session stays in use until Xiaomi issues a new one.

        A login that fails (Xiaomi down, captcha, verification, wrong password) must not wipe the stored
        serviceToken and passToken: without them the session can't be renewed and only another password
        login, the one that makes Xiaomi ask for a captcha, would be left.
        """
        committed = (self._session_data, self.pass_token)
        if fresh or self._pending_login is None:
            self._session_data = self._new_session_data()
        else:
            self._session_data = self._pending_login
        succeeded = False
        try:
            yield
            succeeded = self._session_data.serviceToken is not None
        finally:
            if succeeded:
                if committed[0] is not None and committed[0] is not self._session_data:
                    committed[0].session.detach()
                self._pending_login = None
            else:
                self._pending_login = self._session_data
                self._session_data, self.pass_token = committed

    async def login_with_credentials(self: Self, username: str, password: str) -> str | None:
        self._username = username
        self._password = password
        _LOGGER.debug("Logging in...")
        async with self._login_attempt(fresh=True):
            sign = await self._login_with_credentials_step_1()
            if not sign.startswith('http'):
                location = await self._login_with_credentials_step_2(sign)
            else:
                location = sign
            await self._login_with_credentials_step_3(location)
        _LOGGER.debug("Logged in.")
        return self._session_data.serviceToken

    async def _login_with_credentials_step_1(self: Self) -> str:
        _LOGGER.debug("Xiaomi cloud login - step 1")
        url = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
        cookies = {
            "userId": self._username
        }

        try:
            response = await self._session_data.get(url, cookies=cookies)
            _LOGGER.debug("Xiaomi cloud login - step 1 status: %s", response.status)
            response_text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if _is_outage(response.status):
            raise _unavailable(response.status)
        try:
            response_json = to_json(response_text)
        except Exception:
            raise _unavailable(response.status)

        successful = response.status == 200 and "_sign" in response_json
        if successful:
            return response_json["_sign"]

        _LOGGER.debug("Xiaomi cloud login - step 1 sign missing")
        return ""

    async def _login_with_credentials_step_2(self: Self, sign: str, captcha_code: str | None = None) -> str:
        _LOGGER.debug("Xiaomi cloud login - step 2")
        url = "https://account.xiaomi.com/pass/serviceLoginAuth2"
        params = {
            "sid": "xiaomiio",
            "hash": hashlib.md5(str.encode(self._password)).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": self._username,
            "_sign": sign,
            "_json": "true"
        }
        if captcha_code:
            params["captCode"] = captcha_code
        try:
            response = await self._session_data.post(url, params=params)
            _LOGGER.debug("Xiaomi cloud login - step 2 status: %s", response.status)
            response_text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if _is_outage(response.status):
            raise _unavailable(response.status)
        try:
            response_json = to_json(response_text)
        except Exception:
            # Only Xiaomi's JSON can say the password is wrong; anything else is an outage.
            raise _unavailable(response.status)
        _LOGGER.debug("Xiaomi cloud login - step 2 code: %s", response_json.get("code"))
        if response.status == 200:
            if "ssecurity" in response_json:
                location = response_json["location"]
                self._session_data.ssecurity = response_json["ssecurity"]
                self._session_data.userId = response_json["userId"]
                self._session_data.cUserId = response_json["cUserId"]
                self._session_data.expiration = self._expiration_from(response)
                self.pass_token = response_json.get("passToken") or self._pass_token_from_cookies() or self.pass_token
                self.two_factor_auth_url = None
                return location
            else:
                if (two_factor_url := response_json.get("notificationUrl", None)) is not None:
                    _LOGGER.warning("Xiaomi requires additional identity verification before logging in")
                    await self._raise_two_factor_exception(two_factor_url)
                elif response_json.get("code") == 70016:
                    # Wrong password. Once Xiaomi guards the account with a captcha this answer carries a
                    # captchaUrl too; taking it for a wrong captcha kept the user on the captcha form (#712).
                    raise InvalidCredentialsException()
                elif (captcha_url := response_json.get("captchaUrl", None)) is not None:
                    _LOGGER.warning("Xiaomi requires a captcha before logging in")
                    self._sign = sign
                    if captcha_url.startswith("/"):
                        captcha_url = "https://account.xiaomi.com" + response_json["captchaUrl"]
                    try:
                        captcha_response = await self._session_data.get(captcha_url)
                        captcha_data = await captcha_response.read()
                    except NETWORK_ERRORS as e:
                        raise FailedConnectionException(e)
                    raise CaptchaRequiredException(captcha_url, captcha_data)
        raise InvalidCredentialsException()

    async def _login_with_credentials_step_3(self: Self, location: str) -> None:
        _LOGGER.debug("Xiaomi cloud login - step 3")
        try:
            response = await self._session_data.get(location)
            _LOGGER.debug("Xiaomi cloud login - step 3 status: %s", response.status)
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if response.status == 200 and "serviceToken" in response.cookies:
            self._session_data.serviceToken = response.cookies.get("serviceToken").value
            self.pass_token = self.pass_token or self._pass_token_from_cookies()
        elif response.status == 200 or _is_outage(response.status):
            # The password was already accepted in step 2; STS not issuing the token is Xiaomi's problem.
            raise _unavailable(response.status)
        else:
            raise InvalidCredentialsException()

    async def _qr_get_json(self: Self, url: StrOrURL, step: str) -> dict[str, Any]:
        """A QR login request: an outage or an answer that is not Xiaomi's JSON is a connection problem."""
        try:
            response = await self._session_data.get(url, timeout=QR_REQUEST_TIMEOUT)
            response_text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        try:
            response_json = None if _is_outage(response.status) else to_json(response_text)
        except ValueError:
            response_json = None
        if response_json is None:
            _LOGGER.debug("Xiaomi cloud qr login - %s: Xiaomi unavailable (status %s)", step, response.status)
            raise _unavailable(response.status)
        if response.status != 200 or not isinstance(response_json, dict):
            _LOGGER.debug("Xiaomi cloud qr login - %s failed (status %s)", step, response.status)
            raise FailedLoginException(f"QR login: {step} failed")
        return response_json

    async def login_with_qr_get_code(self: Self) -> tuple[bytes, str]:
        await self.create_session()

        json_resp = await self._qr_get_json(
            "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true", "step 1")
        try:
            location_parsed = dict(URL(json_resp["location"]).query)
            data = {
                "_qrsize": "480",
                "qs": json_resp["qs"],
                "bizDeviceType": "",
                "callback": json_resp["callback"],
                "_json": "true",
                "theme": "",
                "sid": "xiaomiio",
                "needTheme": "false",
                "showActiveX": "false",
                "serviceParam": location_parsed["serviceParam"],
                "_local": "zh_CN",
                "_sign": json_resp["_sign"],
                "_dc": str(int(time.time() * 1000)),
            }
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("Xiaomi cloud qr login - step 1 answer incomplete (code %s)", json_resp.get("code"))
            raise FailedLoginException("Step 1 of QR login failed")

        url = URL("https://account.xiaomi.com/longPolling/loginUrl").with_query(data)
        json_resp = await self._qr_get_json(url, "QR code URL")
        if json_resp.get("code") != 0 or not all(json_resp.get(key) for key in ("qr", "lp", "loginUrl")):
            _LOGGER.debug("Xiaomi cloud qr login - QR code unavailable (code %s)", json_resp.get("code"))
            raise FailedLoginException("QR code unavailable")

        try:
            qr_response = await self._session_data.get(json_resp["qr"], timeout=QR_REQUEST_TIMEOUT)
            qr_data = await qr_response.read()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if qr_response.status != 200:
            if _is_outage(qr_response.status):
                raise _unavailable(qr_response.status)
            _LOGGER.debug("Xiaomi cloud qr login - QR image unavailable (status %s)", qr_response.status)
            raise FailedLoginException("QR code unavailable")
        self._long_polling_url = json_resp["lp"]
        self._qr_deadline = time.monotonic() + self._qr_lifetime(json_resp.get("timeout"))

        return qr_data, await self._browser_login_url(json_resp["loginUrl"])

    @staticmethod
    def _qr_lifetime(timeout: Any) -> float:
        try:
            lifetime = float(timeout)
        except (TypeError, ValueError):
            return QR_CODE_LIFETIME.total_seconds()
        return min(max(lifetime, 0), QR_CODE_LIFETIME_MAX.total_seconds())

    async def _browser_login_url(self: Self, login_url: str) -> str:
        """The QR login link for a person: Xiaomi's sign-in page, not the JSON the connector asked for.

        loginUrl redirects to serviceLogin with ``_json=true`` (the connector requested JSON), which a
        signed-out browser shows as a JSON document instead of the sign-in form (upstream PR #766).
        The callback and followup, which approve the waiting long poll, stay as they are.
        """
        try:
            response = await self._session_data.get(login_url, allow_redirects=False, timeout=QR_REQUEST_TIMEOUT)
        except NETWORK_ERRORS:
            _LOGGER.debug("Xiaomi cloud qr login - login link not resolved, using it as is")
            return login_url
        location = response.headers.get("Location")
        if response.status not in (301, 302, 303, 307, 308) or not location:
            return login_url
        target = URL(login_url).join(URL(location))
        if "_json" not in target.query:
            return login_url
        return str(target.with_query({k: v for k, v in target.query.items() if k != "_json"}))

    async def login_with_qr_wait_for_completion(self: Self) -> None:
        """Waits until the QR code is approved in the Xiaomi Home app, or until it expires.

        Xiaomi holds each long poll open; one that ends without an answer is asked again until the code
        expires (QrLoginTimeoutException). A refusal raises FailedLoginException, an outage or a lost
        connection FailedConnectionException.
        """
        if self._long_polling_url is None:
            raise FailedLoginException("No QR code requested")
        while True:
            remaining = self._qr_deadline - time.monotonic()
            try:
                resp = await self._session_data.get(
                    self._long_polling_url,
                    timeout=ClientTimeout(total=max(min(remaining, QR_POLL_TIMEOUT), 1)),
                    headers={'Connection': 'keep-alive'},
                )
                response_text = await resp.text()
            except (asyncio.TimeoutError, TimeoutError):
                if time.monotonic() >= self._qr_deadline:
                    _LOGGER.debug("Xiaomi cloud qr login - QR code expired")
                    raise QrLoginTimeoutException()
                _LOGGER.debug("Xiaomi cloud qr login - still waiting for the QR code to be scanned")
                continue
            except NETWORK_ERRORS as e:
                raise FailedConnectionException(e)
            break
        try:
            json_resp = None if _is_outage(resp.status) else to_json(response_text)
        except ValueError:
            json_resp = None
        if json_resp is None:
            _LOGGER.debug("Xiaomi cloud qr login - long poll: Xiaomi unavailable (status %s)", resp.status)
            raise _unavailable(resp.status)
        if resp.status != 200 or not isinstance(json_resp, dict):
            _LOGGER.debug("Xiaomi cloud qr login - Long polling failed (status %s)", resp.status)
            raise FailedLoginException("Long polling failed, please try again")

        if json_resp.get("code") != 0:
            _LOGGER.debug("Xiaomi cloud qr login - Long polling succeeded, but returned error %s",
                          json_resp.get("code"))
            raise FailedLoginException("Long polling succeeded, but returned error")
        if not all(json_resp.get(key) for key in ("location", "userId", "ssecurity")):
            _LOGGER.debug("Xiaomi cloud qr login - Long polling answer incomplete")
            raise FailedLoginException("Long polling answer incomplete")
        expiration = self._expiration_from(resp)

        try:
            resp = await self._session_data.get(json_resp["location"], timeout=QR_REQUEST_TIMEOUT)
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if _is_outage(resp.status):
            raise _unavailable(resp.status)
        service_token = resp.cookies.get("serviceToken")
        if resp.status != 200 or service_token is None or not service_token.value:
            _LOGGER.debug("Xiaomi cloud qr login - no serviceToken issued (status %s)", resp.status)
            raise FailedLoginException("Xiaomi did not issue a serviceToken")

        self._session_data.userId = str(json_resp["userId"])
        self._session_data.cUserId = json_resp.get("cUserId") or self._cookie("cUserId")
        self._session_data.ssecurity = json_resp["ssecurity"]
        self.pass_token = json_resp.get("passToken") or self._pass_token_from_cookies()
        self._session_data.serviceToken = service_token.value
        self._session_data.expiration = expiration
        self._long_polling_url = None
        _LOGGER.debug("Xiaomi cloud qr login - logged in")

    def _cookie(self: Self, name: str) -> str | None:
        for cookie in self._session_data.session.cookie_jar:
            if cookie.key == name and cookie.value:
                return cookie.value
        return None

    async def continue_login_with_captcha(self: Self, captcha_code: str) -> str | None:
        _LOGGER.debug("Continuing login with captcha entered.")

        async with self._login_attempt(fresh=False):
            location = await self._login_with_credentials_step_2(self._sign, captcha_code)
            await self._login_with_credentials_step_3(location)

        _LOGGER.debug("Logged in.")
        return self._session_data.serviceToken

    async def _passport_get(self: Self, url: StrOrURL, step: str, **kwargs: Any) -> tuple[ClientResponse, str]:
        try:
            response = await self._session_data.get(url, **kwargs)
            text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        _LOGGER.debug("Xiaomi cloud verification - %s status: %s", step, response.status)
        if _is_outage(response.status):
            raise _unavailable(response.status)
        return response, text

    async def _passport_post(self: Self, url: StrOrURL, step: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._session_data.post(url, **kwargs)
            text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if _is_outage(response.status) or (response_json := _passport_json(text)) is None:
            _LOGGER.debug("Xiaomi cloud verification - %s: Xiaomi unavailable (status %s)", step, response.status)
            raise _unavailable(response.status)
        _LOGGER.debug("Xiaomi cloud verification - %s status: %s code: %s", step, response.status,
                      response_json.get("code"))
        return response_json

    async def _raise_two_factor_exception(self, notification_url: str) -> None:
        """Asks Xiaomi to send the verification code and raises TwoFactorAuthRequiredException.

        identity/list names the method (email or phone) and sets the identity session cookie that
        sending and checking the code need (as XiaomiGateway3 and hass-xiaomi-miot do it). When Xiaomi
        does not send the code (it wants a captcha first, or refuses), the exception says so: the user
        then verifies through the link instead of waiting for a code that never comes.
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        context = parse_qs(urlparse(notification_url).query).get("context", [""])[0]
        await self._passport_get(notification_url, "authStart")
        _, text = await self._passport_get(
            f"{ACCOUNT_URL}/identity/list",
            "identity list",
            params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
        )
        identity = _passport_json(text) or {}
        options = [o for o in (identity.get("options") or [identity.get("flag")]) if o in (FLAG_PHONE, FLAG_EMAIL)]
        flag = identity.get("flag") if identity.get("flag") in options else (options[0] if options else FLAG_EMAIL)
        channel = "Phone" if flag == FLAG_PHONE else "Email"
        # Selects the method inside the identity session before the code is sent.
        await self._passport_get(
            f"{ACCOUNT_URL}/identity/auth/verify{channel}", f"verify{channel} options",
            params={"_flag": str(flag), "_json": "true"},
        )
        sent = await self._passport_post(
            f"{ACCOUNT_URL}/identity/auth/send{channel}Ticket",
            f"send{channel}Ticket",
            params={"_dc": str(int(time.time() * 1000)), "sid": "xiaomiio", "context": context, "mask": "0",
                    "_locale": "en_US"},
            data={"_flag": str(flag), "retry": "0", "icode": "", "_json": "true"},
        )
        raise TwoFactorAuthRequiredException(
            url=notification_url,
            headers=headers,
            context=context,
            flag=flag,
            code_sent=sent.get("code") == 0,
        )

    async def continue_login_with_two_factor(self, code: str, exception: TwoFactorAuthRequiredException) -> bool:
        """Checks the verification code and finishes the login.

        Raises InvalidTwoFactorCodeException when Xiaomi refuses the code, VerificationIncompleteException
        when it accepted the code but no session came out of it, FailedConnectionException when Xiaomi
        can't be reached.
        """
        async with self._login_attempt(fresh=False):
            await self._finish_two_factor(code, exception)
        return True

    async def _finish_two_factor(self, code: str, exception: TwoFactorAuthRequiredException) -> None:
        channel = "Phone" if exception.flag == FLAG_PHONE else "Email"
        verified = await self._passport_post(
            f"{ACCOUNT_URL}/identity/auth/verify{channel}",
            f"verify{channel}",
            params={"_flag": str(exception.flag), "_json": "true", "sid": "xiaomiio", "context": exception.context,
                    "mask": "0", "_locale": "en_US"},
            data={"_flag": str(exception.flag), "ticket": code, "trust": "false", "_json": "true"},
        )
        if verified.get("code") != 0:
            raise InvalidTwoFactorCodeException()
        if not verified.get("location"):
            raise VerificationIncompleteException()
        # The location's host and first hop vary (a regional account host, result/check or Auth2/end
        # directly, a tips page first); follow it wherever it goes until STS issues the serviceToken (#780).
        await self._follow_login_redirects(verified["location"])
        self._session_data.expiration = self._expiration_from_cookies()
        self.pass_token = self._pass_token_from_cookies() or self.pass_token
        if not self._session_data.has_service_token():
            # The redirects set the passToken, but not the whole session: serviceLogin completes it,
            # as hass-xiaomi-miot does after every verification.
            if self._session_data.userId is None or not await self.renew_session():
                raise VerificationIncompleteException()
        _LOGGER.debug("Xiaomi cloud verification - logged in")

    async def _follow_login_redirects(self: Self, url: str) -> None:
        """Follows redirects one by one: the ssecurity is only in a hop's `extension-pragma` header."""
        retried = False
        for _ in range(MAX_LOGIN_HOPS):
            response, text = await self._passport_get(url, "login redirect", allow_redirects=False)
            if pragma := response.headers.get("extension-pragma"):
                try:
                    ssecurity = json.loads(pragma).get("ssecurity")
                except (ValueError, AttributeError):
                    ssecurity = None
                if ssecurity:
                    self._session_data.ssecurity = ssecurity
            self._update_ids_from_cookies()
            service_token = response.cookies.get("serviceToken")
            if service_token is not None and service_token.value:
                self._session_data.serviceToken = service_token.value
                return
            location = response.headers.get("Location") if response.status in REDIRECT_STATUSES else None
            if location is None and response.status == 200 and "Xiaomi Account - Tips" in text and not retried:
                retried = True  # Auth2/end may show a tips page once before it redirects.
                continue
            if location is None:
                # A confirmation page (/fe/...) can carry where to go next as `skipUrl`.
                location = URL(str(response.url)).query.get("skipUrl") if "/fe/" in str(response.url) else None
            if not location:
                _LOGGER.debug("Xiaomi cloud verification - redirects ended without a session (status %s)",
                              response.status)
                return
            url = str(URL(str(response.url or url)).join(URL(location)))
        _LOGGER.debug("Xiaomi cloud verification - too many redirects")

    def _update_ids_from_cookies(self: Self) -> None:
        if user_id := self._cookie("userId"):
            self._session_data.userId = user_id
        if c_user_id := self._cookie("cUserId"):
            self._session_data.cUserId = c_user_id

    def _expiration_from_cookies(self: Self) -> datetime.datetime:
        """When the session ends, from the userId cookie's max-age or expires."""
        now = datetime.datetime.now()
        for cookie in self._session_data.session.cookie_jar:
            if cookie.key != "userId":
                continue
            try:
                if cookie.get("max-age"):
                    max_age = int(cookie["max-age"])
                elif cookie.get("expires"):
                    max_age = int(datetime.datetime.strptime(
                        cookie["expires"], "%a, %d %b %Y %H:%M:%S %Z").timestamp() - now.timestamp())
                else:
                    continue
            except (TypeError, ValueError):
                continue
            if max_age > 0:
                return now + datetime.timedelta(seconds=max_age)
        return now + DEFAULT_SESSION_LIFETIME

    def is_authenticated(self: Self) -> bool:
        return self._session_data is not None and self._session_data.is_authenticated()

    def has_service_token(self: Self) -> bool:
        return self._session_data is not None and self._session_data.has_service_token()

    def is_session_expiring(self: Self) -> bool:
        return self._session_data is None or self._session_data.is_expiring()

    def has_pass_token(self: Self) -> bool:
        return (self._pass_token_from_cookies() or self.pass_token) is not None

    def _pass_token_from_cookies(self: Self) -> str | None:
        if self._session_data is None:
            return None
        for cookie in self._session_data.session.cookie_jar:
            if cookie.key == "passToken" and cookie.value:
                return cookie.value
        return None

    @staticmethod
    def _expiration_from(response: ClientResponse) -> datetime.datetime:
        user_id_cookie = response.cookies.get("userId")
        max_age = 0
        if user_id_cookie is not None:
            try:
                max_age = int(user_id_cookie.get("max-age") or 0)
            except (TypeError, ValueError):
                max_age = 0
        lifetime = datetime.timedelta(seconds=max_age) if max_age > 0 else DEFAULT_SESSION_LIFETIME
        return datetime.datetime.now() + lifetime

    async def renew_session(self: Self) -> bool:
        """Gets a new serviceToken with the long-lived passToken: no password, so no captcha or 2FA.

        Returns False when there is no passToken or Xiaomi does not accept it (then only a full login helps).
        Raises FailedConnectionException when Xiaomi can't be reached, so callers don't fall back to a
        password login because of a network hiccup.
        """
        pass_token = self._pass_token_from_cookies() or self.pass_token
        if pass_token is None or self._session_data is None or self._session_data.userId is None:
            _LOGGER.debug("Xiaomi cloud session renewal - no passToken stored")
            return False
        _LOGGER.debug("Xiaomi cloud session renewal - step 1")
        url = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
        cookies = {
            "userId": str(self._session_data.userId),
            "passToken": pass_token,
        }
        try:
            response = await self._session_data.get(url, cookies=cookies)
            response_text = await response.text()
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        if _is_outage(response.status):
            raise _unavailable(response.status)
        try:
            response_json = to_json(response_text)
        except Exception:
            # Only Xiaomi's JSON can refuse the passToken; anything else must not cost a password login.
            raise _unavailable(response.status)
        if (response.status != 200 or response_json.get("code") != 0 or not response_json.get("ssecurity")
                or not response_json.get("location")):
            _LOGGER.info("Xiaomi did not renew the cloud session with the stored passToken (status %s, code %s)",
                         response.status, response_json.get("code"))
            return False

        _LOGGER.debug("Xiaomi cloud session renewal - step 2")
        try:
            sts_response = await self._session_data.get(response_json["location"])
        except NETWORK_ERRORS as e:
            raise FailedConnectionException(e)
        service_token = sts_response.cookies.get("serviceToken")
        if sts_response.status == 200 and service_token is not None:
            pass
        elif sts_response.status == 200 or _is_outage(sts_response.status):
            raise _unavailable(sts_response.status)
        else:
            _LOGGER.info("Xiaomi did not issue a new serviceToken (status %s)", sts_response.status)
            return False

        self._session_data.serviceToken = service_token.value
        self._session_data.ssecurity = response_json["ssecurity"]
        if (user_id := response_json.get("userId")) is not None:
            self._session_data.userId = str(user_id)
        self._session_data.cUserId = response_json.get("cUserId", self._session_data.cUserId)
        self._session_data.expiration = self._expiration_from(response)
        self.pass_token = response_json.get("passToken") or self._pass_token_from_cookies() or pass_token
        self._session_data.apply_from_parameters()
        _LOGGER.info("Xiaomi cloud session renewed, valid until %s", self._session_data.expiration)
        return True

    async def get_raw_map_data(self: Self, map_url: str | None) -> bytes | None:
        if map_url is not None:
            try:
                _LOGGER.debug("Downloading raw map...")
                response = await self._session_data.session.get(map_url)
            except Exception:
                _LOGGER.debug("Downloading the map failed.")
                return None
            if response.status == 200:
                _LOGGER.debug("Downloaded raw map, status: (%d).", response.status)
                data = await response.content.read()
                _LOGGER.debug("Downloaded raw map (%d).", len(data))
                return data
            else:
                _LOGGER.debug("Downloading the map failed. Status: (%d).", response.status)

        return None

    async def _get_homes(self: Self, server: str, strict: bool = False) -> list[XiaomiCloudHome]:
        url = self.get_api_url(server) + "/v2/homeroom/gethome"
        params = {
            "data": '{"fg": true, "fetch_share": true, "fetch_share_dev": true, "limit": 300, "app_ver": 7}'
        }

        homes = []
        try:
            homes_response = await self.execute_api_call_encrypted(url, params)
        except Exception as e:
            if strict:
                raise
            _LOGGER.debug("Error when retrieving homes from server %s: %s", server, e)
            homes_response = None
        if homes_response is None:
            return homes
        if homes_list := path_extractor(homes_response, "result.homelist"):
            homes.extend([XiaomiCloudHome(int(home["id"]), home["uid"]) for home in homes_list])
        if homes_list := path_extractor(homes_response, "result.share_home_list"):
            homes.extend([XiaomiCloudHome(int(home["id"]), home["uid"]) for home in homes_list])
        return homes

    async def _get_devices_from_home(
        self: Self, server: str, home_id: int, owner_id: int, strict: bool = False
    ) -> list[XiaomiCloudDeviceInfo]:
        url = self.get_api_url(server) + "/v2/home/home_device_list"
        params = {
            "data": json.dumps(
                {
                    "home_id": home_id,
                    "home_owner": owner_id,
                    "limit": 200,
                    "get_split_device": True,
                    "support_smart_home": True,
                }
            )
        }
        try:
            if (response := await self.execute_api_call_encrypted(url, params)) is None:
                return []
        except (FailedConnectionException, TimeoutError) as e:
            if strict:
                raise
            _LOGGER.error("Failed to get devices from home: %s", e)
            return []

        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            # Not "no devices": Xiaomi answered without the list, so this home can't be trusted as empty.
            raise FailedConnectionException(UnexpectedResponseError(200))
        if (raw_devices := result.get("device_info")) is None:
            return []
        return [
            XiaomiCloudDeviceInfo(
                device_id=device["did"],
                name=device["name"],
                model=device["model"],
                token=device.get("token", ""),
                spec_type=device.get("spec_type", ""),
                local_ip=device.get("localip", None),
                mac=device.get("mac", None),
                server=server,
                user_id=owner_id,
                home_id=home_id,
            )
            for device in raw_devices
        ]

    async def get_devices(self: Self, server: Optional[str] = None) -> list[XiaomiCloudDeviceInfo]:
        """The account's devices on one server, or on every server when none is given.

        Errors always reach the caller for a single server (map updates): a timeout or an expired
        session is not "this account has no such device". Across every server (setup), a region that
        fails is skipped, but only while another one lists devices: an empty list built from failed
        regions would read as "no vacuums in this account".
        """
        if server is not None:
            return await self._get_devices_from_server(server, strict=True)
        results = await asyncio.gather(
            *(self._get_devices_from_server(country, strict=True) for country in AVAILABLE_SERVERS),
            return_exceptions=True,
        )
        devices = [device for result in results if isinstance(result, list) for device in result]
        failures = [result for result in results if isinstance(result, BaseException)]
        for failure in failures:
            if not isinstance(failure, Exception):
                raise failure
            _LOGGER.debug("Device list of a region failed: %s", type(failure).__name__)
        if devices or not failures:
            return devices
        if session_error := next((f for f in failures if isinstance(f, SessionExpiredException)), None):
            raise session_error
        if connection_error := next((f for f in failures if isinstance(f, FailedConnectionException)), None):
            raise connection_error
        raise FailedConnectionException(failures[0])

    async def _get_devices_from_server(self: Self, server: str, strict: bool = False) -> list[XiaomiCloudDeviceInfo]:
        homes = await self._get_homes(server, strict)
        device_coro_list = [self._get_devices_from_home(server, home.home_id, home.owner, strict) for home in homes]
        device_lists = await asyncio.gather(*device_coro_list)
        return [device for device_list in device_lists for device in device_list]

    async def get_device_details(self: Self, device_id: str, server: Optional[str] = None) -> XiaomiCloudDeviceInfo | None:
        devices = await self.get_devices(server)
        matching_device = filter(lambda device: device.device_id == device_id, devices)
        return next(matching_device, None)

    async def get_other_info(self: Self, device_id: str, method: str, parameters: dict) -> Any:
        url = self.get_api_url() + "/v2/home/rpc/" + device_id
        params = {
            "data": json.dumps({"method": method, "params": parameters}, separators=(",", ":"))
        }
        return await self.execute_api_call_encrypted(url, params)

    async def execute_api_call_encrypted(self: Self, url: str, params: dict[str, str]) -> Any:
        headers = {
            "Accept-Encoding": "identity",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            k: str(v)
            for k, v in {
                "userId": self._session_data.userId,
                "cUserId": self._session_data.cUserId,
                "yetAnotherServiceToken": self._session_data.serviceToken,
                "serviceToken": self._session_data.serviceToken,
                "locale": self._locale,
                "timezone": self._timezone,
                "is_daylight": time.daylight,
                "dst_offset": time.localtime().tm_isdst * 60 * 60 * 1000,
                "channel": "MI_APP_STORE",
            }.items()
            if v is not None
        }
        millis = round(time.time() * 1000)
        nonce = generate_nonce(millis)
        signed_nonce = self._signed_nonce(nonce)
        fields = generate_enc_params(url, "POST", signed_nonce, nonce, params, self._session_data.ssecurity)

        try:
            _LOGGER.debug("Request URL: %s", url)
            response = await self._session_data.post(url, headers=headers, cookies=cookies, params=fields)
            response_text = await response.text()
        except Exception as e:
            raise FailedConnectionException(e)
        _LOGGER.debug("API response status: %s", response.status)
        if response.status in [401, 403]:
            raise SessionExpiredException()
        if response.status != 200:
            # Not "no such home or device": an empty answer here would end in DeviceNotFound and a reauth.
            raise _unavailable(response.status)
        try:
            return json.loads(decrypt_rc4(self._signed_nonce(fields["_nonce"]), response_text))
        except Exception:
            pass
        # An unencrypted body is an error report, e.g. {"code": 3, "message": "auth err"}.
        try:
            result = json.loads(response_text)
        except Exception:
            _LOGGER.debug("API response could not be decoded")
            raise _unavailable(response.status)
        if self._is_session_error(result):
            raise SessionExpiredException()
        _LOGGER.debug("API returned an error (code %s)", result.get("code") if isinstance(result, dict) else None)
        return None

    @staticmethod
    def _is_session_error(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        message = str(result.get("message", ""))
        return (result.get("code") in (2, 3) or "auth err" in message
                or message in ("invalid signature", "SERVICETOKEN_EXPIRED"))

    def get_api_url(self: Self, server: str | None = None) -> str:
        if server is None:
            server = self.server
        return "https://" + ("" if server == SERVER_CN else (server + ".")) + "api.io.mi.com/app"

    def _signed_nonce(self: Self, nonce: str) -> str:
        hash_object = hashlib.sha256(base64.b64decode(self._session_data.ssecurity) + base64.b64decode(nonce))
        return base64.b64encode(hash_object.digest()).decode()

    def to_config(self: Self) -> XiaomiCloudConnectorConfig:
        return XiaomiCloudConnectorConfig(
            self._username,
            self._password,
            self.server,
            self._session_data.userId,
            self._session_data.cUserId,
            self._session_data.serviceToken,
            self._session_data.expiration,
            self._session_data.ssecurity,
            self.device_id,
            self._pass_token_from_cookies() or self.pass_token,
        )

    @staticmethod
    async def from_config(config: XiaomiCloudConnectorConfig, session_creator: Callable[[], ClientSession]):
        connector = XiaomiCloudConnector(session_creator,
                                         server=config.server)
        connector._username = config.username
        connector._password = config.password
        connector.device_id = config.device_id
        connector.pass_token = config.pass_token

        await connector.create_session()

        connector._session_data.userId = config.user_id
        connector._session_data.cUserId = config.c_user_id
        connector._session_data.serviceToken = config.service_token
        connector._session_data.ssecurity = config.ssecurity
        connector._session_data.expiration = config.expiration

        connector._session_data.apply_from_parameters()

        return connector
