"""Repairs: failures that need the user, each with the most direct fix.

- `wrong_map_format`: a vacuum whose map fails in the Xiaomi format gets its vendor's format.
- `device_not_found`, `vacuum_token`, `map_download_failing`: the same failure FAILURE_THRESHOLD map
  updates in a row. The fix looks the vacuum up in every Xiaomi region with the stored session and
  offers what the cloud knows (region, IP address, token); a login is asked for only when the
  session can't be renewed or the account doesn't have the vacuum. Every issue goes away once the
  map works again.
- `map_saving_disabled`: an Ijai vacuum reports map saving off in the Xiaomi Home app, FAILURE_THRESHOLD
  map updates in a row. Only the app can fix it, so the issue just says so; the next good map clears it.
- `map_encrypted`: the cloud serves the map of this model encrypted with a scheme the integration does
  not know (Dreame md1808, upstream #450). Nothing the user changes fixes it, so the issue names the
  model and where support for it is tracked; the next good map clears it.
- `vacuum_unreachable`: the vacuum has not answered on the local network for LAN_UNREACHABLE_UPDATES
  updates. Same fix as a refused token (IP address and token); it goes away once the vacuum answers,
  since cloud maps can keep working without it.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from aiohttp import ClientError, ClientSession
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_MAC, CONF_MODEL, CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import format_mac
from miio import DeviceException

from .connector.model import XiaomiCloudMapExtractorConnectorStatus as Status
from .connector.model import XiaomiCloudMapExtractorData
from .connector.utils.exceptions import (
    DeviceNotFoundException,
    FailedConnectionException,
    FailedLoginException,
    InvalidDeviceTokenException,
    EncryptedMapUnsupportedException,
    MapSavingDisabledException,
    SessionExpiredException,
)
from .connector.vacuums.base.model import VacuumApi
from .connector.xiaomi_cloud.connector import XiaomiCloudConnector, XiaomiCloudDeviceInfo
from .const import CONF_SERVER, CONF_USED_MAP_API, DOMAIN
from .store import restore_connector_config, save_connector_config

_LOGGER = logging.getLogger(__name__)

WRONG_MAP_FORMAT = "wrong_map_format"
DEVICE_NOT_FOUND = "device_not_found"
VACUUM_TOKEN = "vacuum_token"
MAP_DOWNLOAD_FAILING = "map_download_failing"
VACUUM_UNREACHABLE = "vacuum_unreachable"
MAP_SAVING_DISABLED = "map_saving_disabled"
MAP_ENCRYPTED = "map_encrypted"
MAP_FAILURES = (DEVICE_NOT_FOUND, VACUUM_TOKEN, MAP_DOWNLOAD_FAILING, MAP_SAVING_DISABLED, MAP_ENCRYPTED)
PERSISTENT_FAILURES = (*MAP_FAILURES, VACUUM_UNREACHABLE)
# Fixed outside Home Assistant: the issue explains, it has no fix flow.
UNFIXABLE_FAILURES = (MAP_SAVING_DISABLED, MAP_ENCRYPTED)
# Where support for a model is tracked, for the failures only a new release can fix.
ISSUES_URL = "https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues"
# Fixed with the vacuum's own IP address and token, which can be typed in without the cloud.
LAN_FAILURES = (VACUUM_TOKEN, VACUUM_UNREACHABLE)
# Map updates in a row that fail the same way before it is worth asking the user.
FAILURE_THRESHOLD = 3
# Map updates in a row without an answer from the vacuum on the local network: 5 minutes at the
# default interval, so a Wi-Fi drop or a restart of the vacuum raises nothing.
LAN_UNREACHABLE_UPDATES = 30


def wrong_map_format_issue_id(entry_id: str) -> str:
    return f"{WRONG_MAP_FORMAT}_{entry_id}"


def issue_ids(entry_id: str) -> list[str]:
    """Every issue this integration may raise for an entry."""
    return [f"{key}_{entry_id}" for key in (WRONG_MAP_FORMAT, *PERSISTENT_FAILURES)]


def persistent_failure(outcome: Exception | Status) -> str | None:
    """Which persistent failure an update outcome counts towards, if any."""
    if isinstance(outcome, DeviceNotFoundException):
        return DEVICE_NOT_FOUND
    if isinstance(outcome, InvalidDeviceTokenException) or (
        isinstance(outcome, FailedConnectionException) and isinstance(outcome.base_exception, DeviceException)
    ):
        return VACUUM_TOKEN
    if outcome is Status.FAILED_MAP_DOWNLOAD:
        return MAP_DOWNLOAD_FAILING
    if isinstance(outcome, MapSavingDisabledException) or outcome is Status.MAP_SAVING_DISABLED:
        return MAP_SAVING_DISABLED
    if isinstance(outcome, EncryptedMapUnsupportedException) or outcome is Status.MAP_ENCRYPTED:
        return MAP_ENCRYPTED
    return None


@callback
def async_report_update(
    hass: HomeAssistant, entry: ConfigEntry, outcome: Exception | XiaomiCloudMapExtractorData
) -> None:
    """Counts failures in a row and raises an issue once they keep happening; clears it once fixed."""
    counters: dict[str, dict[str, int]] = hass.data.setdefault(DOMAIN, {}).setdefault("failures", {})
    previous = counters.get(entry.entry_id, {})
    counts: dict[str, int] = {}
    if isinstance(outcome, XiaomiCloudMapExtractorData):
        if outcome.lan_reachable is None and VACUUM_UNREACHABLE in previous:
            counts[VACUUM_UNREACHABLE] = previous[VACUUM_UNREACHABLE]  # not asked: nothing new
        elif outcome.lan_reachable is False:
            counts[VACUUM_UNREACHABLE] = previous.get(VACUUM_UNREACHABLE, 0) + 1
        elif outcome.lan_reachable:
            ir.async_delete_issue(hass, DOMAIN, f"{VACUUM_UNREACHABLE}_{entry.entry_id}")
        if outcome.status is Status.OK:
            for key in MAP_FAILURES:
                ir.async_delete_issue(hass, DOMAIN, f"{key}_{entry.entry_id}")
        outcome = outcome.status
    elif VACUUM_UNREACHABLE in previous:
        counts[VACUUM_UNREACHABLE] = previous[VACUUM_UNREACHABLE]  # the cloud failed: nothing new
    if (failure := persistent_failure(outcome)) is not None:
        counts[failure] = previous.get(failure, 0) + 1
    counters[entry.entry_id] = counts
    for failure, count in counts.items():
        threshold = LAN_UNREACHABLE_UPDATES if failure == VACUUM_UNREACHABLE else FAILURE_THRESHOLD
        if count >= threshold:
            _async_raise(hass, entry, failure, count)


def _async_raise(hass: HomeAssistant, entry: ConfigEntry, failure: str, count: int) -> None:
    issue_id = f"{failure}_{entry.entry_id}"
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        _LOGGER.warning("%s: %s for %d map updates in a row, see Settings → Repairs", entry.title,
                        failure.replace("_", " "), count)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=failure not in UNFIXABLE_FAILURES,
        # A vacuum Home Assistant can't reach may be on another network on purpose: not an error.
        severity=ir.IssueSeverity.WARNING if failure == VACUUM_UNREACHABLE else ir.IssueSeverity.ERROR,
        translation_key=failure,
        translation_placeholders={
            "name": entry.title,
            "server": str(entry.data.get(CONF_SERVER)),
            "host": str(entry.data.get(CONF_HOST)),
            "model": str(entry.data.get(CONF_MODEL)),
            "issues_url": ISSUES_URL,
        },
        data={"entry_id": entry.entry_id, "failure": failure},
    )


def _better_format(entry: ConfigEntry) -> VacuumApi | None:
    """The detected format of a vacuum set to the Xiaomi one, when its vendor has its own."""
    if entry.data.get(CONF_USED_MAP_API) != VacuumApi.XIAOMI:
        return None
    detected = VacuumApi.detect(entry.data.get(CONF_MODEL, ""))
    if detected in (None, VacuumApi.XIAOMI, VacuumApi.UNSUPPORTED):
        return None
    return detected


@callback
def async_check_map_format(hass: HomeAssistant, entry: ConfigEntry, status: Status) -> None:
    """Offer the vendor's map format after the Xiaomi one failed; withdraw it once the map works.

    Only a real parse failure raises the issue, so a vacuum whose map works in the Xiaomi format
    is never touched.
    """
    issue_id = wrong_map_format_issue_id(entry.entry_id)
    if status is Status.OK:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if status is not Status.FAILED_MAP_PARSING or (detected := _better_format(entry)) is None:
        return

    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        _LOGGER.warning(
            "The map of %s (%s) could not be read in the Xiaomi map format; this model uses the %s "
            "format. Open Settings → Repairs to switch it.",
            entry.title, entry.data[CONF_MODEL], detected.title(),
        )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=WRONG_MAP_FORMAT,
        translation_placeholders={
            "name": entry.title,
            "model": entry.data[CONF_MODEL],
            "used_api": VacuumApi.XIAOMI.title(),
            "detected_api": detected.title(),
        },
        data={"entry_id": entry.entry_id, "detected_api": detected.value},
    )


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
