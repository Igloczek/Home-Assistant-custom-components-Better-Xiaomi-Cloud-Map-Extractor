"""After logging in: the device list, choosing the vacuum, confirming its settings, creating the entry."""
from __future__ import annotations

import logging
from typing import Any, Self

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, SOURCE_REAUTH
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from miio import RoborockVacuum
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from .base import FlowBase
from .errors import device_list_error
from ..connector.vacuums.base.model import VacuumApi
from ..const import (
    CONF_USED_MAP_API,
    CONF_SERVER,
    CONF_COLORS,
    CONF_IMAGE_CONFIG,
    CONF_ROOM_COLORS,
    CONF_DRAWABLES,
    CONF_SIZES,
    CONF_TEXTS,
    CONF_IMAGE_CONFIG_SCALE,
    CONF_IMAGE_CONFIG_ROTATE,
    CONF_IMAGE_CONFIG_TRIM_LEFT,
    CONF_IMAGE_CONFIG_TRIM_BOTTOM,
    CONF_IMAGE_CONFIG_TRIM_TOP,
    CONF_IMAGE_CONFIG_TRIM_RIGHT,
)
from ..store import save_connector_config
from ..types import XiaomiCloudMapExtractorConfigEntry

# Logged under the config flow's name, as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__.rpartition('.')[0]}.config_flow")


# noinspection PyTypeChecker,PyBroadException
class VacuumSteps(FlowBase):

    async def async_step_after_auth(self: Self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._after_auth()

    async def _after_auth(self: Self) -> ConfigFlowResult:
        try:
            devices_raw = await self._connector.get_devices()
        except Exception as e:  # every error becomes a message on the retry form
            self._devices_error = device_list_error(e)
            return await self.async_step_devices_failed()

        self._cloud_vacuums = [
            device for device in devices_raw if "vacuum" in device.spec_type
        ]

        if len(self._cloud_vacuums) == 0:
            return self.async_abort(reason="cloud_no_devices")

        if len(self._cloud_vacuums) == 1:
            self._cloud_vacuum = self._cloud_vacuums[0]
            return await self.async_step_confirm_data()

        return await self.async_step_select_vacuum()

    async def async_step_devices_failed(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Logged in, but the device list failed: submitting tries again (or logs in again)."""
        if user_input is not None:
            if self._devices_error == "session_rejected":
                return await self.async_step_auth_method_selection()
            return await self._after_auth()
        return self.async_show_form(step_id="devices_failed", errors={"base": self._devices_error})

    async def async_step_select_vacuum(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle multiple cloud devices found."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._cloud_vacuum = next(filter(
                lambda v: f"{v.server}_{v.device_id}" == user_input["select_vacuum"], self._cloud_vacuums))
            return await self.async_step_confirm_data()

        options: list[SelectOptionDict] = [
            SelectOptionDict(value=f"{cloud_vacuum.server}_{cloud_vacuum.device_id}",
                             label=f"[{cloud_vacuum.server}] {cloud_vacuum.name} - {cloud_vacuum.model} ({cloud_vacuum.mac})")
            for cloud_vacuum in self._cloud_vacuums
        ]

        select_schema = vol.Schema(
            {vol.Required("select_vacuum"): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=False,
                    sort=True,
                    mode=SelectSelectorMode.LIST,
                ))}
        )

        return self.async_show_form(
            step_id="select_vacuum", data_schema=select_schema, errors=errors
        )

    async def async_step_confirm_data(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get(CONF_HOST)
            token = user_input.get(CONF_TOKEN)
            used_map_api = user_input.get(CONF_USED_MAP_API)

            if not await self._validate_vacuum(host, token, VacuumApi(used_map_api)):
                errors["base"] = "invalid_vacuum"
            else:
                unique_id = format_mac(self._cloud_vacuum.mac)

                if self.source == SOURCE_REAUTH:
                    existing_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                    # Entries imported from YAML have no unique_id, but usually know the MAC.
                    configured_mac = existing_entry.unique_id or existing_entry.data.get(CONF_MAC)
                    if configured_mac and format_mac(configured_mac) != unique_id:
                        return self.async_abort(reason="reauth_wrong_vacuum")
                else:
                    await self.async_set_unique_id(unique_id, raise_on_progress=False)
                    self._abort_if_unique_id_configured()
                    # Entries imported from YAML have no unique_id; adopt the one with the same host.
                    existing_entry = next(
                        filter(
                            lambda entry: entry.unique_id is None and entry.data.get(CONF_HOST) == host,
                            self._async_current_entries(include_ignore=False),
                        ),
                        None,
                    )
                self._connector.server = self._cloud_vacuum.server
                try:
                    await save_connector_config(self.hass, self._cloud_vacuum.mac, self._connector.to_config())
                except Exception:  # without the session the entry could not log in
                    _LOGGER.exception("Could not save the Xiaomi Cloud session")
                    errors["base"] = "session_not_saved"
                if errors:
                    pass
                elif existing_entry:
                    data = existing_entry.data.copy()
                    data[CONF_HOST] = host
                    data[CONF_TOKEN] = token
                    data[CONF_DEVICE_ID] = self._cloud_vacuum.device_id
                    data[CONF_MODEL] = self._cloud_vacuum.model
                    data[CONF_MAC] = format_mac(self._cloud_vacuum.mac)
                    data[CONF_NAME] = self._cloud_vacuum.name
                    data[CONF_USERNAME] = self._username
                    data[CONF_PASSWORD] = self._password
                    data[CONF_SERVER] = self._cloud_vacuum.server
                    data[CONF_USED_MAP_API] = used_map_api
                    result_entry = self.async_update_reload_and_abort(
                        existing_entry,
                        unique_id=self._unique_id_for(existing_entry, unique_id),
                        data=data,
                    )
                else:
                    result_entry = self.async_create_entry(
                        title=self._cloud_vacuum.name,
                        data={
                            CONF_HOST: host,
                            CONF_TOKEN: token,
                            CONF_DEVICE_ID: self._cloud_vacuum.device_id,
                            CONF_MODEL: self._cloud_vacuum.model,
                            CONF_MAC: format_mac(self._cloud_vacuum.mac),
                            CONF_NAME: self._cloud_vacuum.name,
                            CONF_USERNAME: self._username,
                            CONF_PASSWORD: self._password,
                            CONF_SERVER: self._cloud_vacuum.server,
                            CONF_USED_MAP_API: used_map_api,
                        },
                        options={
                            CONF_IMAGE_CONFIG: self._default_image_config(),
                            CONF_COLORS: self._default_colors(),
                            CONF_ROOM_COLORS: {},
                            CONF_DRAWABLES: [
                                e.value
                                for e in Drawable
                                if e
                                not in [
                                    Drawable.ROOM_NAMES,
                                    Drawable.NO_CARPET_AREAS,
                                    Drawable.IGNORED_OBSTACLES,
                                    Drawable.IGNORED_OBSTACLES_WITH_PHOTO,
                                ]
                            ],
                            CONF_SIZES: {k.value: v for k, v in Sizes.SIZES.items()},
                            CONF_TEXTS: [],
                        },
                    )
                if not errors:
                    return result_entry

        detected_api = VacuumApi.detect(self._cloud_vacuum.model)
        api_options: list[SelectOptionDict] = [
            SelectOptionDict(value=v, label=v.label + (" *" if v == detected_api else "")) for v in VacuumApi
        ]
        confirm_data_schema = vol.Schema({
            vol.Required(CONF_HOST, default=self._cloud_vacuum.local_ip): str,
            vol.Required(CONF_TOKEN, default=self._cloud_vacuum.token): vol.All(str, vol.Length(min=32, max=32)),
            vol.Required(CONF_USED_MAP_API, default=detected_api): SelectSelector(
                SelectSelectorConfig(
                    options=api_options,
                    custom_value=False,
                    sort=False,
                    mode=SelectSelectorMode.LIST,
                ))
        })

        return self.async_show_form(
            step_id="confirm_data", data_schema=confirm_data_schema, errors=errors, last_step=True
        )

    async def _validate_vacuum(self: Self, host: str, token: str, used_map_api: VacuumApi) -> bool:
        if used_map_api != VacuumApi.ROBOROCK:
            return True
        roborock_vacuum = RoborockVacuum(host, token)
        try:
            status = await self.hass.async_add_executor_job(roborock_vacuum.status)
            return status is not None
        except Exception:
            _LOGGER.exception("Could not validate Roborock vacuum")
            return False

    def _unique_id_for(self: Self, entry: XiaomiCloudMapExtractorConfigEntry, unique_id: str) -> str | None:
        """Give an entry without unique_id (YAML import) the vacuum's MAC, unless another entry already uses it."""
        if entry.unique_id is not None:
            return entry.unique_id
        taken = any(
            other.unique_id == unique_id and other.entry_id != entry.entry_id
            for other in self._async_current_entries(include_ignore=True)
        )
        return None if taken else unique_id

    @staticmethod
    def _default_image_config() -> dict[str, float]:
        image_config = ImageConfig()
        return {
            CONF_IMAGE_CONFIG_SCALE: image_config.scale,
            CONF_IMAGE_CONFIG_ROTATE: image_config.rotate,
            CONF_IMAGE_CONFIG_TRIM_LEFT: image_config.trim.left,
            CONF_IMAGE_CONFIG_TRIM_RIGHT: image_config.trim.right,
            CONF_IMAGE_CONFIG_TRIM_TOP: image_config.trim.top,
            CONF_IMAGE_CONFIG_TRIM_BOTTOM: image_config.trim.bottom,
        }

    @staticmethod
    def _default_colors() -> dict[str, tuple[int, int, int, int]]:
        return {k: ([*v] if len(v) == 4 else [*v, 255]) for k, v in ColorsPalette.COLORS.items()}
