"""Repairs: the fix flow of each issue raised in `issues.py`, with the most direct fix.

- `wrong_map_format`: one confirm stores the vendor's map format.
- `device_not_found`, `vacuum_token`, `map_download_failing`, `vacuum_unreachable`: the vacuum is looked
  up in every Xiaomi region with the stored session, and what the cloud knows (region, IP address,
  token) is offered; a login is asked for only when the session can't be renewed or the account
  doesn't have the vacuum.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from aiohttp import ClientError, ClientSession
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_MAC, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import format_mac

from .connector.utils.exceptions import (
    FailedConnectionException,
    FailedLoginException,
    SessionExpiredException,
)
from .connector.vacuums.base.model import VacuumApi
from .connector.xiaomi_cloud.connector import XiaomiCloudConnector, XiaomiCloudDeviceInfo
from .const import CONF_SERVER, CONF_USED_MAP_API, DOMAIN
from .issues import LAN_FAILURES, PERSISTENT_FAILURES
from .store import restore_connector_config, save_connector_config


class WrongMapFormatRepairFlow(RepairsFlow):
    """One confirm: store the detected format in the entry and reload it."""

    def __init__(self, entry_id: str, detected_api: str) -> None:
        self._entry_id = entry_id
        self._detected_api = VacuumApi(detected_api)

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        entry = self.hass.config_entries.async_get_known_entry(self._entry_id)
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_USED_MAP_API: self._detected_api.value}
            )
            # A loaded entry reloads through its update listener; one still retrying setup has none.
            if entry.state is not ConfigEntryState.LOADED:
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})

        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=issue.translation_placeholders if issue else None,
        )


class VacuumLookupRepairFlow(RepairsFlow):
    """Looks the vacuum up in every region with the stored session and offers what the cloud knows."""

    def __init__(self, entry_id: str, failure: str) -> None:
        self._entry_id = entry_id
        self._failure = failure
        self._device: XiaomiCloudDeviceInfo | None = None
        self._error = "cannot_connect"

    @property
    def _entry(self) -> ConfigEntry:
        return self.hass.config_entries.async_get_known_entry(self._entry_id)

    def _placeholders(self) -> dict[str, str]:
        entry = self._entry
        device = self._device
        return {
            "name": entry.title,
            "server": device.server if device else str(entry.data.get(CONF_SERVER)),
            "host": (device.local_ip if device else None) or str(entry.data.get(CONF_HOST)),
        }

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        try:
            self._device = await self._find_vacuum()
        except (FailedLoginException, FailedConnectionException, ClientError, TimeoutError) as err:
            if self._failure in LAN_FAILURES:
                # The vacuum's own settings can be typed in without the cloud.
                return await self.async_step_vacuum()
            if isinstance(err, FailedLoginException):
                return await self.async_step_login_needed()
            self._error = "cannot_connect"
            return await self.async_step_lookup_failed()
        if self._failure in LAN_FAILURES:
            return await self.async_step_vacuum()
        if self._device is None:
            return await self.async_step_not_in_account()
        if self._cloud_changes():
            return await self.async_step_confirm_device()
        return await self.async_step_settings_match()

    async def _find_vacuum(self) -> XiaomiCloudDeviceInfo | None:
        entry = self._entry
        mac = entry.data.get(CONF_MAC)
        config = await restore_connector_config(self.hass, mac) if mac else None
        if config is None:
            raise FailedLoginException()

        def session_creator() -> ClientSession:
            return async_create_clientsession(self.hass)

        connector = await XiaomiCloudConnector.from_config(config, session_creator)
        try:
            devices = await connector.get_devices()
        except SessionExpiredException:
            if not await connector.renew_session():
                raise
            await save_connector_config(self.hass, mac, connector.to_config())
            devices = await connector.get_devices()
        device_id = entry.data.get(CONF_DEVICE_ID)
        return next(
            (d for d in devices if d.device_id == device_id),
            next((d for d in devices if d.mac and format_mac(d.mac) == format_mac(mac)), None),
        )

    def _cloud_values(self) -> dict[str, str]:
        device = self._device
        values = {CONF_SERVER: device.server, CONF_DEVICE_ID: device.device_id}
        if device.local_ip:
            values[CONF_HOST] = device.local_ip
        if device.token:
            values[CONF_TOKEN] = device.token
        return values

    def _cloud_changes(self) -> bool:
        data = self._entry.data
        return any(data.get(key) != value for key, value in self._cloud_values().items())

    def _apply(self, values: dict[str, str]) -> FlowResult:
        entry = self._entry
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **values})
        # A loaded entry reloads through its update listener; one still retrying setup has none.
        if entry.state is not ConfigEntryState.LOADED:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={})

    async def async_step_confirm_device(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is not None:
            return self._apply(self._cloud_values())
        return self.async_show_form(
            step_id="confirm_device", data_schema=vol.Schema({}), description_placeholders=self._placeholders()
        )

    async def async_step_vacuum(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is not None:
            values = {CONF_HOST: user_input[CONF_HOST], CONF_TOKEN: user_input[CONF_TOKEN]}
            if self._device is not None:
                values = {**self._cloud_values(), **values}
            return self._apply(values)
        data = self._entry.data
        cloud = self._cloud_values() if self._device is not None else {}
        return self.async_show_form(
            step_id="vacuum",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=cloud.get(CONF_HOST, data.get(CONF_HOST))): str,
                vol.Required(CONF_TOKEN, default=cloud.get(CONF_TOKEN, data.get(CONF_TOKEN))): str,
            }),
            description_placeholders=self._placeholders(),
        )

    async def async_step_settings_match(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="settings_match", data_schema=vol.Schema({}), description_placeholders=self._placeholders()
        )

    async def async_step_lookup_failed(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="lookup_failed",
            data_schema=vol.Schema({}),
            errors={"base": self._error},
            description_placeholders=self._placeholders(),
        )

    async def _reauth(self, step_id: str, user_input: dict[str, str] | None) -> FlowResult:
        if user_input is not None:
            self._entry.async_start_reauth(self.hass)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema({}), description_placeholders=self._placeholders()
        )

    async def async_step_not_in_account(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self._reauth("not_in_account", user_input)

    async def async_step_login_needed(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self._reauth("login_needed", user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    assert data is not None
    if (failure := data.get("failure")) in PERSISTENT_FAILURES:
        return VacuumLookupRepairFlow(str(data["entry_id"]), str(failure))
    return WrongMapFormatRepairFlow(str(data["entry_id"]), str(data["detected_api"]))
