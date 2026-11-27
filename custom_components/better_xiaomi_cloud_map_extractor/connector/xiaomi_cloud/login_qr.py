"""QR code login: the code is approved in the Xiaomi Home app, no password involved."""
from __future__ import annotations
import datetime
import logging
import time
from typing import Any, Self

from aiohttp import ClientTimeout
from aiohttp.typedefs import StrOrURL
from yarl import URL

from .network import NETWORK_ERRORS, _is_outage, _unavailable
from .session import XiaomiCloudSession
from .utils import to_json
from ..utils.exceptions import FailedLoginException, FailedConnectionException, QrLoginTimeoutException

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")

# A QR code is valid for the "timeout" Xiaomi sends with it; these apply when it is missing or absurd.
QR_CODE_LIFETIME = datetime.timedelta(minutes=5)
QR_CODE_LIFETIME_MAX = datetime.timedelta(minutes=15)
# Each long poll is held open by Xiaomi; it is asked again when it ends before the code expires.
QR_POLL_TIMEOUT = 60
QR_REQUEST_TIMEOUT = ClientTimeout(total=15)



class QrLogin(XiaomiCloudSession):
    """Gets a QR code (`login_with_qr_get_code`), then waits for its approval."""

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
        try:
            status = response.status
            location = response.headers.get("Location")
        finally:
            response.release()
        if status not in (301, 302, 303, 307, 308) or not location:
            return login_url
        try:
            target = URL(login_url).join(URL(location))
        except ValueError:
            _LOGGER.debug("Xiaomi cloud qr login - login link redirect is no URL, using the link as is")
            return login_url
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
            except TimeoutError:
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
        try:
            if _is_outage(resp.status):
                raise _unavailable(resp.status)
            service_token = resp.cookies.get("serviceToken")
            if resp.status != 200 or service_token is None or not service_token.value:
                _LOGGER.debug("Xiaomi cloud qr login - no serviceToken issued (status %s)", resp.status)
                raise FailedLoginException("Xiaomi did not issue a serviceToken")
            token_value = service_token.value
        finally:
            resp.release()

        self._session_data.userId = str(json_resp["userId"])
        self._session_data.cUserId = json_resp.get("cUserId") or self._cookie("cUserId")
        self._session_data.ssecurity = json_resp["ssecurity"]
        self.pass_token = json_resp.get("passToken") or self._pass_token_from_cookies()
        self._session_data.serviceToken = token_value
        self._session_data.expiration = expiration
        self._long_polling_url = None
        _LOGGER.debug("Xiaomi cloud qr login - logged in")
