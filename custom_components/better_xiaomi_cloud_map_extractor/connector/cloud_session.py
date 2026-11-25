"""The cloud session the map updates use: kept working without asking Xiaomi for a captcha.

A session about to expire is renewed ahead of time with the passToken, and kept in use while that is
not possible. When Xiaomi rejects it, the passToken is tried first; a password login is the last
resort, and one that failed is not repeated on every update, since that is what makes Xiaomi ask
for a captcha. Every new session is handed to `on_session_changed` to be stored.
"""
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Self

from aiohttp import ClientSession

from .model import XiaomiCloudMapExtractorConnectorConfiguration
from .utils.exceptions import FailedConnectionException, FailedLoginException, RetryLaterException
from .xiaomi_cloud.connector import XiaomiCloudConnector, XiaomiCloudConnectorConfig

# Logged under the update loop's name, as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(__package__)

# A proactive renewal that Xiaomi turned down is not retried on every 10 s update.
RENEWAL_RETRY_INTERVAL = timedelta(hours=1)
# Nor is a password login that failed: repeating it every 10 s is what makes Xiaomi ask for a captcha.
PASSWORD_LOGIN_RETRY_INTERVAL = timedelta(minutes=15)

SessionSaver = Callable[[XiaomiCloudConnectorConfig], Awaitable[None]]


class CloudSession:
    """Owns the cloud connector of one vacuum; callers serialize their calls (one update at a time)."""

    cloud_connector: XiaomiCloudConnector | None

    def __init__(
        self: Self,
        session_creator: Callable[[], ClientSession],
        config: XiaomiCloudMapExtractorConnectorConfiguration,
        connector_config: XiaomiCloudConnectorConfig | None,
        on_session_changed: SessionSaver | None = None,
    ) -> None:
        self._config = config
        self._session_creator = session_creator
        self._connector_config = connector_config
        self._on_session_changed = on_session_changed
        self._last_renewal_attempt: datetime | None = None
        self._last_failed_password_login: datetime | None = None
        self.cloud_connector = None

    async def connect(self: Self) -> XiaomiCloudConnector:
        """The connector, created or restored from the stored session once, with a session to use."""
        if self.cloud_connector is None:
            if self._connector_config is None:
                _LOGGER.debug("Creating a new connector...")
                self.cloud_connector = XiaomiCloudConnector(self._session_creator, server=self._config.server)
            else:
                _LOGGER.debug("Restoring connector configuration...")
                self.cloud_connector = await XiaomiCloudConnector.from_config(
                    self._connector_config, self._session_creator
                )

        await self._ensure_session()
        return self.cloud_connector

    async def _ensure_session(self: Self) -> None:
        """Makes sure there is a session to use, renewing it ahead of its expiration when possible.

        Like the Mi Home app, a session that is about to expire keeps being used when renewal is not
        possible right now; only Xiaomi rejecting it (SessionExpiredException) forces a new login.
        """
        if not self.cloud_connector.has_service_token():
            await self.recover()
            return
        if not self.cloud_connector.is_session_expiring() or not self._renewal_allowed():
            return
        try:
            await self._renew_session()
        except FailedConnectionException as e:
            _LOGGER.debug("Could not renew the Xiaomi Cloud session now, keeping the current one: %s", e)

    async def recover(self: Self) -> None:
        """Gets a working session: passToken renewal first, password login only when that is refused."""
        if self.cloud_connector.has_pass_token() and await self._renew_session():
            return
        await self._login_with_password()

    def _renewal_allowed(self: Self) -> bool:
        return (self._last_renewal_attempt is None
                or datetime.now() - self._last_renewal_attempt >= RENEWAL_RETRY_INTERVAL)

    async def _renew_session(self: Self) -> bool:
        self._last_renewal_attempt = datetime.now()
        if not await self.cloud_connector.renew_session():
            return False
        await self._save_session()
        return True

    async def _login_with_password(self: Self) -> None:
        if self._config.username is None or self._config.password is None:
            raise FailedLoginException()
        if (self._last_failed_password_login is not None
                and datetime.now() - self._last_failed_password_login < PASSWORD_LOGIN_RETRY_INTERVAL):
            raise RetryLaterException()
        _LOGGER.info("Logging in to Xiaomi Cloud with the stored credentials")
        try:
            await self.cloud_connector.login_with_credentials(self._config.username, self._config.password)
        except Exception:
            self._last_failed_password_login = datetime.now()
            raise
        self._last_failed_password_login = None
        if not self.cloud_connector.has_service_token():
            _LOGGER.error("Not authenticated!")
            raise FailedLoginException()
        _LOGGER.debug("Logged in.")
        await self._save_session()

    async def _save_session(self: Self) -> None:
        if self._on_session_changed is None:
            return
        try:
            await self._on_session_changed(self.cloud_connector.to_config())
        except Exception as e:
            _LOGGER.warning("Failed to store the Xiaomi Cloud session: %s", e)
