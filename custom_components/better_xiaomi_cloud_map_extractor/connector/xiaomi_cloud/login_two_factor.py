"""Identity verification (two-factor by email or SMS) that Xiaomi can ask for during a password login."""
from __future__ import annotations
import datetime
import email.utils
import json
import logging
import time
from typing import Any, Self
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientResponse
from aiohttp.typedefs import StrOrURL
from homeassistant.util import dt as dt_util
from yarl import URL

from .network import NETWORK_ERRORS, _is_outage, _unavailable
from .session import DEFAULT_SESSION_LIFETIME, XiaomiCloudSession
from ..utils.exceptions import (
    FailedConnectionException,
    InvalidTwoFactorCodeException,
    TwoFactorAuthRequiredException,
    VerificationIncompleteException,
)

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")

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


class TwoFactorLogin(XiaomiCloudSession):
    """Sends the verification code (email or SMS) and finishes the login with it."""

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
            try:
                url = str(URL(str(response.url or url)).join(URL(location)))
            except ValueError:
                _LOGGER.debug("Xiaomi cloud verification - a redirect is no URL")
                return
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
                    # A cookie date is GMT; parsing it naive would read it as local time.
                    expires = email.utils.parsedate_to_datetime(cookie["expires"])
                    max_age = int((expires - dt_util.utcnow()).total_seconds())
                else:
                    continue
            except (TypeError, ValueError):
                continue
            if max_age > 0:
                return now + datetime.timedelta(seconds=max_age)
        return now + DEFAULT_SESSION_LIFETIME
