import logging
from dataclasses import dataclass
from typing import Self

from miio import DeviceException

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connector import XiaomiCloudMapExtractorConnector
from .connector.model import XiaomiCloudMapExtractorData
from .connector.utils.exceptions import (
    XiaomiCloudMapExtractorException,
    FailedLoginException,
    TwoFactorAuthRequiredException,
    InvalidDeviceTokenException,
    InvalidCredentialsException,
    CaptchaRequiredException,
    DeviceNotFoundException,
    FailedConnectionException,
    FailedMapDownloadException,
    FailedMapParseException,
    MapSavingDisabledException,
    RetryLaterException,
)
from .const import DOMAIN, DEFAULT_UPDATE_INTERVAL
from .repairs import async_check_map_format, async_report_update

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthIssue:
    """Why the background update needs the user; the reauth flow reads it to explain what happened."""
    reason: str
    verification_url: str | None = None


def auth_issue_for(err: XiaomiCloudMapExtractorException) -> AuthIssue:
    if isinstance(err, CaptchaRequiredException):
        return AuthIssue("captcha_required")
    if isinstance(err, TwoFactorAuthRequiredException):
        return AuthIssue("verification_required", err.url)
    if isinstance(err, InvalidCredentialsException):
        return AuthIssue("invalid_credentials")
    if isinstance(err, (DeviceNotFoundException, InvalidDeviceTokenException)):
        return AuthIssue("device_not_found")
    return AuthIssue("session_expired")


def update_failure_key(err: Exception) -> str:
    """Why a map update failed, as a translated message (`exceptions` in translations/en.json)."""
    if isinstance(err, RetryLaterException):
        return "login_retry_later"
    if isinstance(err, DeviceNotFoundException):
        return "vacuum_not_in_region"
    if isinstance(err, InvalidDeviceTokenException) or (
            isinstance(err, FailedConnectionException) and isinstance(err.base_exception, DeviceException)):
        return "vacuum_token_refused"
    if isinstance(err, (FailedConnectionException, ClientError, TimeoutError)):
        return "cloud_unavailable"
    if isinstance(err, FailedMapDownloadException):
        return "map_download_failed"
    if isinstance(err, MapSavingDisabledException):
        return "map_saving_disabled"
    if isinstance(err, FailedMapParseException):
        return "map_parse_failed"
    if isinstance(err, DeviceException):
        return "vacuum_unreachable"
    _LOGGER.error("Unexpected error while updating the map", exc_info=err)
    return "unexpected_error"


class XiaomiCloudMapExtractorDataUpdateCoordinator(DataUpdateCoordinator[XiaomiCloudMapExtractorData]):

    def __init__(
            self: Self,
            hass: HomeAssistant,
            connector: XiaomiCloudMapExtractorConnector,
    ) -> None:
        self.connector = connector
        self.auth_issue: AuthIssue | None = None
        # always_update=False: an update that brings the same map and status writes no state.
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_UPDATE_INTERVAL,
                         update_method=self.update_data, always_update=False)

    async def update_data(self: Self) -> XiaomiCloudMapExtractorData:
        try:
            data = await self.connector.get_data()
        except (
                FailedLoginException,
                InvalidCredentialsException,
                TwoFactorAuthRequiredException,
                CaptchaRequiredException,
        ) as err:
            self.auth_issue = auth_issue_for(err)
            _LOGGER.debug("Triggering reauth flow (%s)...", self.auth_issue.reason)
            raise ConfigEntryAuthFailed(
                str(err), translation_domain=DOMAIN, translation_key=self.auth_issue.reason
            ) from err
        except Exception as err:  # noqa: BLE001 - every failure gets a translated message
            if self.config_entry is not None:
                async_report_update(self.hass, self.config_entry, err)
            key = update_failure_key(err)
            raise UpdateFailed(str(err) or key, translation_domain=DOMAIN, translation_key=key) from err
        self.auth_issue = None
        if self.config_entry is not None:
            async_check_map_format(self.hass, self.config_entry, data.status)
            async_report_update(self.hass, self.config_entry, data)
        return data

    async def force_update_data(self) -> None:
        self.connector.force_refresh()
        await self.async_request_refresh()

    async def set_auto_updating(self, updating: bool) -> None:
        self.connector.set_auto_updating(updating)

    def is_auto_updating(self) -> bool:
        return self.connector.is_auto_updating()
