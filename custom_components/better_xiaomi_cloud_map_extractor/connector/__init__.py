import asyncio
import dataclasses
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Self, Type

from aiohttp import ClientSession

from .model import (
    XiaomiCloudMapExtractorData,
    XiaomiCloudMapExtractorConnectorConfiguration,
    XiaomiCloudMapExtractorConnectorStatus
)
from .utils import to_image
from .utils.exceptions import (
    DeviceNotFoundException,
    FailedConnectionException,
    InvalidCredentialsException,
    RetryLaterException,
    SessionExpiredException,
    InvalidDeviceTokenException,
    TwoFactorAuthRequiredException,
    FailedLoginException,
    FailedMapDownloadException,
    FailedMapParseException,
    MapSavingDisabledException,
    EncryptedMapUnsupportedException,
)
from .vacuums.base.model import VacuumConfig, VacuumApi
from .vacuums.base.vacuum_base import BaseXiaomiCloudVacuum
from .vacuums.vacuum_dreame import DreameCloudVacuum
from .vacuums.vacuum_roborock import RoborockCloudVacuum
from .vacuums.vacuum_roidmi import RoidmiCloudVacuum
from .vacuums.vacuum_unsupported import UnsupportedCloudVacuum
from .vacuums.vacuum_viomi import ViomiCloudVacuum
from .vacuums.vacuum_ijai import IjaiCloudVacuum
from .vacuums.vacuum_xiaomi import XiaomiCloudVacuum
from .xiaomi_cloud.connector import (
    XiaomiCloudConnector,
    XiaomiCloudDeviceInfo,
    XiaomiCloudConnectorConfig,
)

_LOGGER = logging.getLogger(__name__)

# A proactive renewal that Xiaomi turned down is not retried on every 10 s update.
RENEWAL_RETRY_INTERVAL = timedelta(hours=1)
# Nor is a password login that failed: repeating it every 10 s is what makes Xiaomi ask for a captcha.
PASSWORD_LOGIN_RETRY_INTERVAL = timedelta(minutes=15)
# A vacuum that cannot report whether it moves (no status over the local network) gets its map
# downloaded every update only while the map changes. After this many identical downloads it is
# checked once per UNCHANGED_MAP_CHECK_INTERVAL until it changes again (upstream #764, #719).
UNCHANGED_MAP_DOWNLOADS = 3
UNCHANGED_MAP_CHECK_INTERVAL = timedelta(minutes=1)

SessionSaver = Callable[[XiaomiCloudConnectorConfig], Awaitable[None]]

AVAILABLE_VACUUM_PLATFORMS: dict[VacuumApi, Type[BaseXiaomiCloudVacuum]] = {v.vacuum_platform(): v for v in [
    RoborockCloudVacuum,
    ViomiCloudVacuum,
    RoidmiCloudVacuum,
    DreameCloudVacuum,
    IjaiCloudVacuum,
    XiaomiCloudVacuum,
    UnsupportedCloudVacuum
]}


class XiaomiCloudMapExtractorConnector:
    _used_api: VacuumApi
    _config: XiaomiCloudMapExtractorConnectorConfiguration
    _cloud_connector: XiaomiCloudConnector | None
    _vacuum_connector: BaseXiaomiCloudVacuum | None
    _map_cache: XiaomiCloudMapExtractorData
    _status: XiaomiCloudMapExtractorConnectorStatus
    _server: str | None
    _session_creator: Callable[[], ClientSession]
    _connector_config: XiaomiCloudConnectorConfig | None
    _forced_refresh: bool
    _auto_update: bool
    _last_hash: str | None

    def __init__(
        self: Self,
        session_creator: Callable[[], ClientSession],
        config: XiaomiCloudMapExtractorConnectorConfiguration,
        connector_config: XiaomiCloudConnectorConfig | None,
        on_session_changed: SessionSaver | None = None,
    ) -> None:
        self._config = config
        self._on_session_changed = on_session_changed
        self._last_renewal_attempt: datetime | None = None
        self._last_failed_password_login: datetime | None = None
        # The scheduled update and the refresh button can overlap; one session recovery at a time.
        self._lock = asyncio.Lock()
        self._connector_config = connector_config
        self._session_creator = session_creator
        self._cloud_connector = None
        self._vacuum_connector: BaseXiaomiCloudVacuum | None = None
        self._map_cache = XiaomiCloudMapExtractorData()
        self._status: XiaomiCloudMapExtractorConnectorStatus = XiaomiCloudMapExtractorConnectorStatus.UNINITIALIZED
        self._server = self._config.server
        self._used_api = self._config.used_api
        self._forced_refresh = False
        self._auto_update = True
        self._last_hash = None
        self._unchanged_downloads = 0
        self._last_download: datetime | None = None

    async def get_data(self: Self) -> XiaomiCloudMapExtractorData:
        async with self._lock:
            self._map_cache.lan_reachable = None
            if await self._should_get_map():
                _LOGGER.debug("Downloading new map.")
                await self._get_map()
            else:
                _LOGGER.debug("Using cached map.")
            # A copy: the coordinator compares it with the previous one to skip unchanged updates.
            return dataclasses.replace(self._map_cache)

    async def _update(self: Self) -> None:
        if self._cloud_connector is None:
            if self._connector_config is None:
                _LOGGER.debug("Creating a new connector...")
                self._cloud_connector = XiaomiCloudConnector(self._session_creator, server=self._config.server)
            else:
                _LOGGER.debug("Restoring connector configuration...")
                self._cloud_connector = await XiaomiCloudConnector.from_config(
                    self._connector_config, self._session_creator
                )

        await self._ensure_session()
        try:
            await self._download_map()
        except SessionExpiredException:
            # The serviceToken can stop working before its stored expiration.
            _LOGGER.info("Xiaomi Cloud rejected the session, renewing it")
            await self._recover_session()
            await self._download_map()

    async def _download_map(self: Self) -> None:
        if self._vacuum_connector is None or self._status == XiaomiCloudMapExtractorConnectorStatus.UNINITIALIZED:
            _LOGGER.debug("Initializing...")
            await self._initialize()
            _LOGGER.debug("Initialized.")

        _LOGGER.debug("Downloading map...")
        map_data, map_raw_data = await self._vacuum_connector.get_map()
        _LOGGER.debug("Downloaded map.")
        if map_data is None:
            raise FailedMapDownloadException()
        self._map_cache.map_data = map_data
        self._map_cache.map_image = await asyncio.to_thread(to_image, map_data)
        self._map_cache.map_data_raw = map_raw_data

    async def _ensure_session(self: Self) -> None:
        """Makes sure there is a session to use, renewing it ahead of its expiration when possible.

        Like the Mi Home app, a session that is about to expire keeps being used when renewal is not
        possible right now; only Xiaomi rejecting it (SessionExpiredException) forces a new login.
        """
        if not self._cloud_connector.has_service_token():
            await self._recover_session()
            return
        if not self._cloud_connector.is_session_expiring() or not self._renewal_allowed():
            return
        try:
            await self._renew_session()
        except FailedConnectionException as e:
            _LOGGER.debug("Could not renew the Xiaomi Cloud session now, keeping the current one: %s", e)

    async def _recover_session(self: Self) -> None:
        """Gets a working session: passToken renewal first, password login only when that is refused."""
        if self._cloud_connector.has_pass_token() and await self._renew_session():
            return
        await self._login_with_password()

    def _renewal_allowed(self: Self) -> bool:
        return (self._last_renewal_attempt is None
                or datetime.now() - self._last_renewal_attempt >= RENEWAL_RETRY_INTERVAL)

    async def _renew_session(self: Self) -> bool:
        self._last_renewal_attempt = datetime.now()
        if not await self._cloud_connector.renew_session():
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
            await self._cloud_connector.login_with_credentials(self._config.username, self._config.password)
        except Exception:
            self._last_failed_password_login = datetime.now()
            raise
        self._last_failed_password_login = None
        if not self._cloud_connector.has_service_token():
            _LOGGER.error("Not authenticated!")
            raise FailedLoginException()
        _LOGGER.debug("Logged in.")
        await self._save_session()

    async def _save_session(self: Self) -> None:
        if self._on_session_changed is None:
            return
        try:
            await self._on_session_changed(self._cloud_connector.to_config())
        except Exception as e:
            _LOGGER.warning("Failed to store the Xiaomi Cloud session: %s", e)

    async def _initialize(self: Self) -> None:
        _LOGGER.debug("Retrieving device info, server: %s", self._config.server)
        device_details = await self._cloud_connector.get_device_details(self._config.device_id, self._config.server)

        if device_details is not None:
            self._server = device_details.server
            _LOGGER.debug("Retrieved device model: %s", device_details.model)
            self._vacuum_connector = self._create_device(device_details)
            _LOGGER.debug("Created device, used api: %s", self._used_api)
            self._status = XiaomiCloudMapExtractorConnectorStatus.OK
        else:
            _LOGGER.error("Failed to retrieve model")
            raise DeviceNotFoundException()

    async def _should_get_map(self: Self) -> bool:
        if self._forced_refresh:
            self._forced_refresh = False
            return True
        if self._vacuum_connector is None or self._map_cache.map_data is None:
            return True
        if not self._auto_update:
            return False
        moving = await self._vacuum_connector.should_update_map()
        self._map_cache.lan_reachable = self._vacuum_connector.lan_reachable
        if moving is not None:
            return moving
        return (
            self._unchanged_downloads < UNCHANGED_MAP_DOWNLOADS
            or self._last_download is None
            or datetime.now() - self._last_download >= UNCHANGED_MAP_CHECK_INTERVAL
        )

    async def _get_map(self: Self) -> None:
        self._last_download = datetime.now()
        try:
            await self._update()
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.OK
            self._map_cache.last_successful_update_timestamp = datetime.now()
            self._map_cache.two_factor_url = None
            if self._last_hash != (new_hash := hashlib.sha256(self._map_cache.map_data_raw).hexdigest()):
                _LOGGER.debug("Old hash: '%s', New hash: '%s'", self._last_hash, new_hash)
                self._last_hash = new_hash
                self._map_cache.last_real_update_timestamp = self._map_cache.last_successful_update_timestamp
                self._unchanged_downloads = 0
            else:
                _LOGGER.debug("Hash not changed: '%s'", self._last_hash)
                self._unchanged_downloads += 1

        except DeviceNotFoundException as e:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.DEVICE_NOT_FOUND
            raise e
        except InvalidCredentialsException as e:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.INVALID_CREDENTIALS
            raise e
        except FailedLoginException as e:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.FAILED_LOGIN
            raise e
        except InvalidDeviceTokenException as e:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.INVALID_TOKEN
            raise e
        except FailedMapDownloadException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.FAILED_MAP_DOWNLOAD
        except FailedMapParseException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.FAILED_MAP_PARSING
        except MapSavingDisabledException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.MAP_SAVING_DISABLED
            if self._map_cache.map_data is None:
                raise  # nothing to keep showing: the update failure carries the message
        except EncryptedMapUnsupportedException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.MAP_ENCRYPTED
            if self._map_cache.map_data is None:
                raise
        except TwoFactorAuthRequiredException as e:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.TWO_FACTOR_REQUIRED
            self._map_cache.two_factor_url = e.url
            raise e
        finally:
            self._map_cache.last_update_timestamp = datetime.now()
            if self._vacuum_connector:
                self._map_cache.additional_vacuum_data = self._vacuum_connector.additional_data()

    def _create_device(self: Self, device_details: XiaomiCloudDeviceInfo) -> BaseXiaomiCloudVacuum:
        vacuum_config = VacuumConfig(
            self._cloud_connector,
            device_details,
            self._config.server,
            self._config.device_id,
            self._config.host,
            self._config.token,
            self._config.model,
            self._config.colors,
            self._config.drawables,
            self._config.image_config,
            self._config.sizes,
            self._config.texts,
            self._config.fit_to_explored,
        )
        vacuum_class = AVAILABLE_VACUUM_PLATFORMS.get(self._used_api, UnsupportedCloudVacuum)
        return vacuum_class(vacuum_config)

    def force_refresh(self):
        self._forced_refresh = True

    def set_auto_updating(self, updating: bool) -> None:
        self._auto_update = updating

    def is_auto_updating(self) -> bool:
        return self._auto_update
