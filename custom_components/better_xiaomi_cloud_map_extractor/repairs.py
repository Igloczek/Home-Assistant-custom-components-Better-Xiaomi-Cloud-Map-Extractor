"""Repairs: switch a vacuum whose map fails in the Xiaomi format to the format of its vendor."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .connector.model import XiaomiCloudMapExtractorConnectorStatus as Status
from .connector.vacuums.base.model import VacuumApi
from .const import CONF_USED_MAP_API, DOMAIN

_LOGGER = logging.getLogger(__name__)

WRONG_MAP_FORMAT = "wrong_map_format"


def wrong_map_format_issue_id(entry_id: str) -> str:
    return f"{WRONG_MAP_FORMAT}_{entry_id}"


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


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    assert data is not None
    return WrongMapFormatRepairFlow(str(data["entry_id"]), str(data["detected_api"]))
