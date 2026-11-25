"""The cloud session: its tokens, the stored form of it, and the state every login and API call shares.

`XiaomiCloudSession` is the base of `XiaomiCloudConnector`; the login, renewal, device and API mixins
read and replace `_session_data` and `pass_token` through it. A login runs inside `_login_attempt`, so a
failed one never replaces the session in use.
"""
from __future__ import annotations
import datetime
import locale
import os
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from typing import Self, Unpack

import tzlocal
from aiohttp import ClientSession, ClientResponse
from aiohttp.client import _RequestOptions
from aiohttp.typedefs import StrOrURL
from yarl import URL

from .utils import generate_agent, generate_device_id

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")

# serviceLogin is asked to renew the session this long before the stored expiration.
SESSION_RENEWAL_MARGIN = datetime.timedelta(days=1)
DEFAULT_SESSION_LIFETIME = datetime.timedelta(days=20)


def _default_locale() -> str | None:
    """The language code `locale.getdefaultlocale` gave (it goes away in Python 3.15)."""
    for variable in ("LC_ALL", "LC_CTYPE", "LANG", "LANGUAGE"):
        if value := os.environ.get(variable):
            name = locale.normalize(value.split(":")[0])
            if name == "C" or name.upper() == "UTF-8":
                return None
            return name.split("@")[0].split(".")[0]
    return None


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
        return await self.session.get(url, headers={**passed_headers, **self.headers}, **kwargs)

    async def post(self: Self, url: StrOrURL, **kwargs: Unpack[_RequestOptions]) -> ClientResponse:
        passed_headers = kwargs.pop("headers", {})
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



class XiaomiCloudSession:
    """The session state shared by the parts of `XiaomiCloudConnector`."""

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
        self._locale = _default_locale() or "en_GB"
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

    def _cookie(self: Self, name: str) -> str | None:
        for cookie in self._session_data.session.cookie_jar:
            if cookie.key == name and cookie.value:
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
