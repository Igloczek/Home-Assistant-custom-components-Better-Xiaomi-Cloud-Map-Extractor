"""Session renewal with the long-lived passToken: no password, so no captcha or two-factor."""
from __future__ import annotations
import logging
from typing import Self

from .network import NETWORK_ERRORS, _is_outage, _unavailable
from .session import XiaomiCloudSession
from .utils import to_json
from ..utils.exceptions import FailedConnectionException

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")


class SessionRenewal(XiaomiCloudSession):

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
