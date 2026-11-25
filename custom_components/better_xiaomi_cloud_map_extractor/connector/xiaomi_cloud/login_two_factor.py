"""Identity verification (two-factor by email) that Xiaomi can ask for during a password login."""
from __future__ import annotations
import datetime
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlparse

from .session import XiaomiCloudSession
from .utils import generate_agent
from ..utils.exceptions import TwoFactorAuthRequiredException

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")


class TwoFactorLogin(XiaomiCloudSession):
    """Starts the email verification and finishes the login with the code."""

    async def _raise_two_factor_exception(self, notification_url: str) -> None:
        """
        Begins the 2FA email flow by starting the process and raising TwoFactorAuthRequiredException
        with data needed to continue the authentication.
        """
        agent = generate_agent()
        # 1) Open notificationUrl (authStart)
        headers = {
            "User-Agent": agent,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        _LOGGER.debug("Opening notificationUrl (authStart)")
        r = await self._session_data.get(notification_url, headers=headers)
        _LOGGER.debug("authStart status=%s", r.status)

        # 2) Fetch identity options (list)
        context = parse_qs(urlparse(notification_url).query)["context"][0]
        list_params = {
            "sid": "xiaomiio",
            "context": context,
            "_locale": "en_US"
        }
        _LOGGER.debug("GET /identity/list")
        r = await self._session_data.get("https://account.xiaomi.com/identity/list", params=list_params, headers=headers)
        _LOGGER.debug("identity/list status=%s", r.status)
        # self._log_cookies("after identity/list")  # Method not implemented

        # 3) Request email ticket
        send_params = {
            "_dc": str(int(time.time() * 1000)),
            "sid": "xiaomiio",
            "context": list_params["context"],
            "mask": "0",
            "_locale": "en_US"
        }
        send_data = {
            "retry": "0",
            "icode": "",
            "_json": "true",
            "ick": ""  # Cookie access would need proper implementation
        }
        _LOGGER.debug("sendEmailTicket POST")
        r = await self._session_data.post("https://account.xiaomi.com/identity/auth/sendEmailTicket",
                                          params=send_params, data=send_data, headers=headers)
        # self._log_cookies("after sendEmailTicket")  # Method not implemented
        try:
            jr = await r.json()
        except Exception:
            jr = {}
        _LOGGER.debug("sendEmailTicket response status=%s code=%s", r.status, jr.get("code") if isinstance(jr, dict) else None)

        # 4) Ask user for the email code and verify
        _LOGGER.debug("Raising TwoFactorAuthRequiredException")
        raise TwoFactorAuthRequiredException(
            url=notification_url,
            headers=headers,
            context=context
        )

    async def continue_login_with_two_factor(self, code: str, exception: TwoFactorAuthRequiredException) -> bool:
        """Continues the 2FA email flow with the provided verification code."""
        async with self._login_attempt(fresh=False):
            return await self._finish_two_factor(code, exception)

    async def _finish_two_factor(self, code: str, exception: TwoFactorAuthRequiredException) -> bool:
        headers = exception.headers
        context = exception.context

        # Some endpoints are picky about Referer/Origin
        headers.setdefault("Referer", "https://account.xiaomi.com/identity/auth/sendEmailTicket")
        headers.setdefault("Origin", "https://account.xiaomi.com")

        # Grab 'ick' cookie if present – Xiaomi often validates it with the code
        try:
            ick_cookie = self._session_data.session.cookie_jar.filter_cookies(
                "https://account.xiaomi.com"
            ).get("ick")
            ick_value = ick_cookie.value if ick_cookie else ""
        except Exception:
            ick_value = ""

        verify_params = {
            "_flag": "8",
            "_json": "true",
            "sid": "xiaomiio",
            "context": context,
            "mask": "0",
            "_locale": "en_US"
        }
        verify_data = {
            "_flag": "8",
            "ticket": code,
            "trust": "false",
            "_json": "true",
            "ick": ick_value
        }
        # self._log_cookies("before verifyEmail")  # Method not implemented
        r = await self._session_data.post("https://account.xiaomi.com/identity/auth/verifyEmail",
                                          params=verify_params, data=verify_data, headers=headers)
        # self._log_cookies("after verifyEmail")  # Method not implemented
        if r.status != 200:
            _LOGGER.error("verifyEmail failed: status=%s", r.status)
            return False

        finish_loc = None
        try:
            jr = await r.json()
            _LOGGER.debug("verifyEmail response status=%s code=%s", r.status, jr.get("code"))
            finish_loc = jr.get("location")
        except Exception:
            # Non-JSON or empty; try to extract from headers or body
            _LOGGER.debug("verifyEmail returned non-JSON, attempting fallback extraction.")
            finish_loc = r.headers.get("Location")
            text = await r.text()
            if not finish_loc and text:
                m = re.search(r'https://account\.xiaomi\.com/identity/result/check\?[^"\']+', text)
                if m:
                    finish_loc = m.group(0)

        # Fallback: directly hit result/check using existing identity_session/context
        if not finish_loc:
            _LOGGER.debug("Using fallback call to /identity/result/check")
            r0 = await self._session_data.get(
                "https://account.xiaomi.com/identity/result/check",
                params={"sid": "xiaomiio", "context": context, "_locale": "en_US"},
                headers=headers,
                allow_redirects=False
            )
            _LOGGER.debug("result/check (fallback) status=%s redirect=%s", r0.status, "Location" in r0.headers)
            if r0.status in (301, 302) and r0.headers.get("Location"):
                finish_loc = r0.url if "serviceLoginAuth2/end" in str(r0.url) else r0.headers["Location"]

        if not finish_loc:
            _LOGGER.error("Unable to determine finish location after verifyEmail.")
            return False

        # self._log_cookies("before finish_2fa chain")  # Method not implemented

        # First hop: GET identity/result/check (do NOT follow redirects to inspect Location)
        if "identity/result/check" in finish_loc:
            r = await self._session_data.get(finish_loc, headers=headers, allow_redirects=False)
            _LOGGER.debug("result/check status=%s redirect=%s", r.status, "Location" in r.headers)
            end_url = r.headers.get("Location")
        else:
            end_url = finish_loc

        if not end_url:
            _LOGGER.error("Could not find Auth2/end URL in finish chain.")
            return False

        # 6) Call Auth2/end WITHOUT redirects to capture 'extension-pragma' header containing ssecurity
        r = await self._session_data.get(end_url, headers=headers, allow_redirects=False)
        _LOGGER.debug("Auth2/end status=%s", r.status)
        text = await r.text()
        # Some servers return 200 first (HTML 'Tips' page), then 302 on next call.
        if r.status == 200 and "Xiaomi Account - Tips" in text:
            r = await self._session_data.get(end_url, headers=headers, allow_redirects=False)
            _LOGGER.debug("Auth2/end(second) status=%s", r.status)

        ext_prag = r.headers.get("extension-pragma")
        if ext_prag:
            try:
                ep_json = json.loads(ext_prag)
                ssec = ep_json.get("ssecurity")
                psec = ep_json.get("psecurity")
                _LOGGER.debug("extension-pragma present (ssecurity: %s, psecurity: %s)", bool(ssec), bool(psec))
                if ssec:
                    self._session_data.ssecurity = ssec
            except Exception as e:
                _LOGGER.debug("Failed to parse extension-pragma: %s", e)

        if not self._session_data.ssecurity:
            _LOGGER.error("extension-pragma header missing ssecurity; cannot continue.")
            return False

        # 7) Find STS redirect and visit it (to set serviceToken cookie)
        sts_url = r.headers.get("Location")
        if not sts_url:
            text = await r.text()
            if text:
                idx = text.find("https://sts.api.io.mi.com/sts")
                if idx != -1:
                    end = text.find('"', idx)
                    if end == -1:
                        end = idx + 300
                    sts_url = text[idx:end]
        if not sts_url:
            _LOGGER.error("Auth2/end did not provide STS redirect.")
            return False

        r = await self._session_data.get(sts_url, headers=headers, allow_redirects=True)
        _LOGGER.debug("STS status=%s", r.status)
        if r.status != 200:
            _LOGGER.error("STS did not complete: status=%s", r.status)
            return False

        # Extract serviceToken from cookie jar (use full URL for aiohttp CookieJar)
        try:
            sts_cookies = self._session_data.session.cookie_jar.filter_cookies(str(r.url))
            service_token = sts_cookies.get("serviceToken")
            if service_token:
                self._session_data.serviceToken = service_token.value
        except BaseException:
            pass
        found = bool(self._session_data.serviceToken)
        if not found:
            _LOGGER.error("Could not parse serviceToken; cannot complete login.")
            return False
        _LOGGER.debug("STS did not return JSON; assuming 'ok' style response and relying on cookies.")
        _LOGGER.debug("extract_service_token: found=%s", found)

        # Mirror serviceToken to API domains expected by Mi Cloud
        # self.install_service_token_cookies(self._serviceToken)  # Method not implemented

        # Update ids from cookies if available
        user_id_cookie = self._session_data.session.cookie_jar.filter_cookies(
            "https://account.xiaomi.com"
        ).get("userId") or self._session_data.session.cookie_jar.filter_cookies(
            "https://sts.api.io.mi.com"
        ).get(
            "userId"
        )
        if user_id_cookie:
            self._session_data.userId = user_id_cookie.value

        cuserId_cookie = self._session_data.session.cookie_jar.filter_cookies(
            "https://account.xiaomi.com"
        ).get("cUserId") or self._session_data.session.cookie_jar.filter_cookies(
            "https://sts.api.io.mi.com"
        ).get(
            "cUserId"
        )
        if cuserId_cookie:
            self._session_data.cUserId = cuserId_cookie.value

        max_age = next(
            (
                int(cookie.get("max-age", 0))
                for cookie in self._session_data.session.cookie_jar
                if cookie.key == "userId" and cookie.get("max-age") is not None
            ),
            0,
        )
        if max_age == 0:
            max_age = next(
                (
                    int(cookie.get("expires", 0))
                    for cookie in self._session_data.session.cookie_jar
                    if cookie.key == "userId" and cookie.get("expires") is not None
                ),
                0,
            ) - int(datetime.datetime.now().timestamp())
        if max_age <= 0:
            max_age = 20 * 24 * 60 * 60
        self._session_data.expiration = datetime.datetime.now() + datetime.timedelta(
            seconds=max_age
        )
        self.pass_token = self._pass_token_from_cookies() or self.pass_token

        return True
