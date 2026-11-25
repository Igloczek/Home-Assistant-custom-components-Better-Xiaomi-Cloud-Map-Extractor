"""Password login (three steps, with captcha and identity verification on the way)."""
from __future__ import annotations
import hashlib
import logging
from typing import Self

from .login_two_factor import TwoFactorLogin
from .network import NETWORK_ERRORS, _is_outage, _unavailable
from .utils import to_json
from ..utils.exceptions import (
    InvalidCredentialsException,
    FailedConnectionException,
    CaptchaRequiredException,
)

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")


class CredentialsLogin(TwoFactorLogin):
    """Logs in with the username and password; a captcha continues with `continue_login_with_captcha`."""

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

    async def continue_login_with_captcha(self: Self, captcha_code: str) -> str | None:
        _LOGGER.debug("Continuing login with captcha entered.")

        async with self._login_attempt(fresh=False):
            location = await self._login_with_credentials_step_2(self._sign, captcha_code)
            await self._login_with_credentials_step_3(location)

        _LOGGER.debug("Logged in.")
        return self._session_data.serviceToken
