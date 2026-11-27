"""The map update: when to download the map, and what its status is.

The cloud session it downloads with is kept working by `cloud_session.CloudSession`; the vendor class
that downloads and parses the map is picked from `platforms.AVAILABLE_VACUUM_PLATFORMS`.
"""
import asyncio
import dataclasses
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Self

from aiohttp import ClientSession

from .cloud_session import CloudSession, SessionSaver
from .model import (
    XiaomiCloudMapExtractorData,
    XiaomiCloudMapExtractorConnectorConfiguration,
    XiaomiCloudMapExtractorConnectorStatus
)
from .platforms import AVAILABLE_VACUUM_PLATFORMS
from .utils import to_image
from .utils.exceptions import (
    DeviceNotFoundException,
    InvalidCredentialsException,
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
from .vacuums.vacuum_unsupported import UnsupportedCloudVacuum
from .xiaomi_cloud.connector import (
    XiaomiCloudDeviceInfo,
    XiaomiCloudConnectorConfig,
)

_LOGGER = logging.getLogger(__name__)

# A vacuum that cannot report whether it moves (no status over the local network) gets its map
# downloaded every update only while the map changes. After this many identical downloads it is
# checked once per UNCHANGED_MAP_CHECK_INTERVAL until it changes again (upstream #764, #719).
UNCHANGED_MAP_DOWNLOADS = 3
UNCHANGED_MAP_CHECK_INTERVAL = timedelta(minutes=1)


class XiaomiCloudMapExtractorConnector:
    _used_api: VacuumApi
    _config: XiaomiCloudMapExtractorConnectorConfiguration
    _cloud: CloudSession
    _vacuum_connector: BaseXiaomiCloudVacuum | None
    _map_cache: XiaomiCloudMapExtractorData
    _status: XiaomiCloudMapExtractorConnectorStatus
    _server: str | None
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
        # The scheduled update and the refresh button can overlap; one session recovery at a time.
        self._lock = asyncio.Lock()
        self._cloud = CloudSession(session_creator, config, connector_config, on_session_changed)
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
        await self._cloud.connect()
        try:
            await self._download_map()
        except SessionExpiredException:
            # The serviceToken can stop working before its stored expiration.
            _LOGGER.info("Xiaomi Cloud rejected the session, renewing it")
            await self._cloud.recover()
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

    async def _initialize(self: Self) -> None:
        _LOGGER.debug("Retrieving device info, server: %s", self._config.server)
        device_details = await self._cloud.cloud_connector.get_device_details(self._config.device_id, self._config.server)

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
            # The image too: Xiaomi vacuums draw the robot and path from MIoT properties, so the map
            # moves while the cloud map object stays the same.
            new_hash = hashlib.sha256(self._map_cache.map_data_raw + (self._map_cache.map_image or b"")).hexdigest()
            if self._last_hash != new_hash:
                _LOGGER.debug("Old hash: '%s', New hash: '%s'", self._last_hash, new_hash)
                self._last_hash = new_hash
                self._map_cache.last_real_update_timestamp = self._map_cache.last_successful_update_timestamp
                self._unchanged_downloads = 0
            else:
                _LOGGER.debug("Hash not changed: '%s'", self._last_hash)
                self._unchanged_downloads += 1

        except DeviceNotFoundException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.DEVICE_NOT_FOUND
            raise
        except InvalidCredentialsException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.INVALID_CREDENTIALS
            raise
        except FailedLoginException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.FAILED_LOGIN
            raise
        except InvalidDeviceTokenException:
            self._map_cache.status = XiaomiCloudMapExtractorConnectorStatus.INVALID_TOKEN
            raise
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
            raise
        finally:
            self._map_cache.last_update_timestamp = datetime.now()
            if self._vacuum_connector:
                self._map_cache.additional_vacuum_data = self._vacuum_connector.additional_data()

    def _create_device(self: Self, device_details: XiaomiCloudDeviceInfo) -> BaseXiaomiCloudVacuum:
        vacuum_config = VacuumConfig(
            self._cloud.cloud_connector,
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
