"""Encrypted (RC4 signed) calls to the Xiaomi cloud API with the session, and the map download."""
from __future__ import annotations
import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Any, Self

from aiohttp import ClientTimeout

from .const import SERVER_CN
from .network import _unavailable
from .session import XiaomiCloudSession
from .utils import generate_nonce, generate_enc_params, decrypt_rc4
from ..utils.exceptions import FailedConnectionException, SessionExpiredException

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")
# A map file is larger than an API answer; aiohttp alone would wait five minutes.
MAP_DOWNLOAD_TIMEOUT = ClientTimeout(total=60, sock_connect=10)


class EncryptedApi(XiaomiCloudSession):

    async def get_raw_map_data(self: Self, map_url: str | None) -> bytes | None:
        if map_url is None:
            return None
        try:
            _LOGGER.debug("Downloading raw map...")
            response = await self._session_data.session.get(map_url, timeout=MAP_DOWNLOAD_TIMEOUT)
            try:
                if response.status != 200:
                    _LOGGER.debug("Downloading the map failed. Status: (%d).", response.status)
                    return None
                data = await response.content.read()
            finally:
                response.release()
        except Exception as err:  # a failed download; the last map stays
            _LOGGER.debug("Downloading the map failed: %s", type(err).__name__)
            return None
        _LOGGER.debug("Downloaded raw map (%d).", len(data))
        return data

    async def get_other_info(
            self: Self, device_id: str, method: str, parameters: Any, server: str | None = None) -> Any:
        """A miIO command the cloud forwards to the vacuum over its own connection (as Mi Home does)."""
        url = self.get_api_url(server) + "/v2/home/rpc/" + device_id
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
        except (ValueError, binascii.Error, UnicodeDecodeError):
            _LOGGER.debug("API response was not RC4-encrypted JSON, reading plain error payload")
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
