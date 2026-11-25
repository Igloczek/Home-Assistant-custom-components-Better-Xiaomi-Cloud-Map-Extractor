"""The state the config flow steps share."""
from __future__ import annotations

from asyncio import Task

from homeassistant.config_entries import ConfigEntry, ConfigFlow

from ..connector.utils.exceptions import CaptchaRequiredException, TwoFactorAuthRequiredException
from ..connector.xiaomi_cloud.connector import XiaomiCloudConnector, XiaomiCloudDeviceInfo
from ..coordinator import AuthIssue


class FlowBase(ConfigFlow):
    """Set up once per flow; each step module reads and fills these."""

    def __init__(self) -> None:
        """Initialize."""
        self._username: str | None = None
        self._password: str | None = None
        self._cloud_vacuums: list[XiaomiCloudDeviceInfo] = []
        self._cloud_vacuum: XiaomiCloudDeviceInfo | None = None
        self._connector: XiaomiCloudConnector | None = None
        self._captcha_exception: CaptchaRequiredException | None = None
        self._two_factor_exception: TwoFactorAuthRequiredException | None = None
        self._wait_for_login_task: Task | None = None
        self._qr_placeholders: dict[str, str] = {}
        self._qr_error: str | None = None
        self._devices_error: str = "cannot_connect"
        self._original: ConfigEntry | None = None
        self._reauth_issue: AuthIssue | None = None
