"""The Xiaomi cloud connector: one object for logging in, keeping the session and calling the API.

Its parts live in their own modules:

- `session`: tokens, the stored config, and the login attempt that keeps a working session on failure
- `login_credentials` and `login_two_factor`: password login, captcha, identity verification
- `login_qr`: QR code login
- `renewal`: new serviceToken from the passToken
- `devices`: homes and devices per region
- `api`: encrypted API calls and the map download
"""
from __future__ import annotations
from collections.abc import Callable
from typing import Self

from aiohttp import ClientSession

from .devices import DeviceDiscovery, XiaomiCloudDeviceInfo, XiaomiCloudHome
from .login_credentials import CredentialsLogin
from .login_qr import QrLogin
from .network import NETWORK_ERRORS
from .renewal import SessionRenewal
from .session import (
    DEFAULT_SESSION_LIFETIME,
    SESSION_RENEWAL_MARGIN,
    XiaomiCloudConnectorConfig,
    XiaomiCloudSessionData,
)

__all__ = [
    "DEFAULT_SESSION_LIFETIME",
    "NETWORK_ERRORS",
    "SESSION_RENEWAL_MARGIN",
    "XiaomiCloudConnector",
    "XiaomiCloudConnectorConfig",
    "XiaomiCloudDeviceInfo",
    "XiaomiCloudHome",
    "XiaomiCloudSessionData",
]


# noinspection PyBroadException
class XiaomiCloudConnector(CredentialsLogin, QrLogin, SessionRenewal, DeviceDiscovery):
    """Everything the integration does with the Xiaomi cloud; stored and restored with `to_config`/`from_config`."""

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
