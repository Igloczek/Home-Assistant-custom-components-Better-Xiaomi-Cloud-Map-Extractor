"""Importing a vacuum from the original integration (its config entry or its YAML camera)."""
from __future__ import annotations

from typing import Any, Self, Mapping

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .base import FlowBase
from ..const import NAME, ORIGINAL_NAME
from ..legacy import create_config_entry_data_from_yaml
from ..migration import (
    async_copy_original_session,
    async_disable_original,
    importable_original_entries,
    unique_id_taken,
    vacuum_mac,
)


class OriginalImportSteps(FlowBase):

    async def async_step_import(
            self, import_info: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Import an entry."""

        def session_creator() -> ClientSession:
            return async_create_clientsession(self.hass)

        # Checked before logging in: Home Assistant runs this for every YAML camera at each start.
        self._async_abort_entries_match({CONF_HOST: import_info[CONF_HOST]})
        data, options = await create_config_entry_data_from_yaml(import_info, session_creator)
        if (mac := data.get(CONF_MAC)) is not None and any(
            vacuum_mac(entry) == format_mac(mac) for entry in self._async_current_entries(include_ignore=False)
        ):
            return self.async_abort(reason="already_configured")
        if mac is not None and not unique_id_taken(self.hass, format_mac(mac)):
            await self.async_set_unique_id(format_mac(mac), raise_on_progress=False)
        return self.async_create_entry(
            title=NAME,
            data=data,
            options=options,
        )

    async def async_step_import_original(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a vacuum set up in the original Xiaomi Cloud Map Extractor and import it."""
        originals = importable_original_entries(self.hass)
        if not originals:
            return self.async_abort(reason="no_original_entries")
        errors: dict[str, str] = {}
        if user_input is not None:
            self._original = next((e for e in originals if e.entry_id == user_input["original_entry"]), None)
            if self._original is not None:
                return await self._async_import_original()
        schema = vol.Schema({
            vol.Required("original_entry", default=originals[0].entry_id): SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=e.entry_id, label=self._original_label(e)) for e in originals],
                    mode=SelectSelectorMode.LIST,
                ))
        })
        return self.async_show_form(
            step_id="import_original",
            data_schema=schema,
            errors=errors,
            description_placeholders={"original_name": ORIGINAL_NAME},
        )

    async def async_step_integration_discovery(
            self: Self, discovery_info: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """A vacuum is still set up in the original integration (started by async_setup)."""
        original = self.hass.config_entries.async_get_entry(discovery_info["entry_id"])
        if original is None or original not in importable_original_entries(self.hass):
            return self.async_abort(reason="no_original_entries")
        await self.async_set_unique_id(vacuum_mac(original))
        self._abort_if_unique_id_configured()
        self._original = original
        self.context["title_placeholders"] = {"name": self._original_label(original)}
        return await self.async_step_import_original_confirm()

    async def async_step_import_original_confirm(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_import_original()
        return self.async_show_form(
            step_id="import_original_confirm",
            description_placeholders={"name": self._original_label(self._original), "original_name": ORIGINAL_NAME},
        )

    async def _async_import_original(self: Self) -> ConfigFlowResult:
        """Copy the original entry and its cloud session, and disable the original entry.

        The user confirmed this step, which says the original entry will be disabled. The device and
        entities move over when the new entry sets up (`migration.async_adopt_original_entities`).
        """
        original = self._original
        if original not in importable_original_entries(self.hass):
            return self.async_abort(reason="already_configured")
        # Entries the original imported from YAML have no unique id; the copy gets the MAC. Without
        # a MAC (the YAML import's cloud lookup failed), setup asks to log in, which fills it in.
        await self.async_set_unique_id(vacuum_mac(original), raise_on_progress=False)
        self._abort_if_unique_id_configured()
        if (mac := vacuum_mac(original)) is not None:
            await async_copy_original_session(self.hass, mac)
        await async_disable_original(self.hass, original)
        return self.async_create_entry(title=original.title, data=dict(original.data), options=dict(original.options))

    @staticmethod
    def _original_label(entry: ConfigEntry) -> str:
        """Entries the original imported from YAML are all titled like the integration; add the host."""
        if (host := entry.data.get(CONF_HOST)) is None:
            return entry.title
        return f"{entry.title} ({host})"
