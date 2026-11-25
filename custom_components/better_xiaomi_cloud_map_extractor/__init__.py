from __future__ import annotations

import logging

from aiohttp import ClientSession
from homeassistant.const import (
    CONF_HOST,
    CONF_TOKEN,
    CONF_MAC,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_MODEL,
    CONF_DEVICE_ID
)
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig, TrimConfig
from vacuum_map_parser_base.config.size import Sizes, Size

from .connector import XiaomiCloudMapExtractorConnector
from .connector.model import XiaomiCloudMapExtractorConnectorConfiguration
from .connector.xiaomi_cloud.connector import XiaomiCloudConnectorConfig
from .connector.vacuums.base.model import VacuumApi
from .const import (
    ORIGINAL_DOMAIN,
    CONF_SERVER,
    CONF_USED_MAP_API,
    PLATFORMS,
    CONF_SIZES,
    CONF_COLORS,
    CONF_DRAWABLES,
    CONF_IMAGE_CONFIG,
    CONF_IMAGE_CONFIG_SCALE,
    CONF_IMAGE_CONFIG_ROTATE,
    CONF_IMAGE_CONFIG_TRIM_LEFT,
    CONF_IMAGE_CONFIG_TRIM_RIGHT,
    CONF_IMAGE_CONFIG_TRIM_TOP,
    CONF_IMAGE_CONFIG_TRIM_BOTTOM,
    CONF_IMAGE_CONFIG_FIT_TO_EXPLORED,
    CONF_ROOM_COLORS,
    DOMAIN,
)
from .coordinator import XiaomiCloudMapExtractorDataUpdateCoordinator
from .migration import (
    async_adopt_original_entities,
    async_fill_unique_id,
    async_flag_leftover_originals,
    async_original_changed,
    importable_original_entries,
)
from .original_yaml import async_check_original_yaml, async_find_original_yaml, original_yaml_issue_id
from .repairs import issue_ids
from .store import restore_connector_config, save_connector_config
from .types import XiaomiCloudMapExtractorConfigEntry, XiaomiCloudMapExtractorRuntimeData

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Offer every vacuum still set up in the original integration under "Discovered"."""

    @callback
    def original_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        if changed.domain == ORIGINAL_DOMAIN:
            async_original_changed(hass, changed, removed=change is ConfigEntryChange.REMOVED)

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, original_changed)
    async_find_original_yaml(hass, config)
    async_flag_leftover_originals(hass)

    for original in importable_original_entries(hass):
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={"entry_id": original.entry_id},
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XiaomiCloudMapExtractorConfigEntry) -> bool:
    async_fill_unique_id(hass, entry)
    async_check_original_yaml(hass, entry)
    await async_adopt_original_entities(hass, entry)
    xcme_configuration = to_configuration(entry)

    def session_creator() -> ClientSession:
        return async_create_clientsession(hass)

    connector_config = await restore_connector_config(hass, xcme_configuration.mac)

    async def session_saver(config: XiaomiCloudConnectorConfig) -> None:
        # Renewed sessions and background logins must survive a restart, otherwise every restart
        # after the stored session expires means another password login (and another captcha).
        await save_connector_config(hass, xcme_configuration.mac, config)

    xcme_connector = XiaomiCloudMapExtractorConnector(
        session_creator, xcme_configuration, connector_config, session_saver
    )
    xcme_update_coordinator = XiaomiCloudMapExtractorDataUpdateCoordinator(hass, xcme_connector)
    # Set before the first refresh so a reauth flow started by it can read why it was started.
    entry.runtime_data = XiaomiCloudMapExtractorRuntimeData(xcme_update_coordinator)
    await xcme_update_coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XiaomiCloudMapExtractorConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: XiaomiCloudMapExtractorConfigEntry) -> None:
    for issue_id in issue_ids(entry.entry_id):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    ir.async_delete_issue(hass, DOMAIN, original_yaml_issue_id(entry))


async def async_reload_entry(hass: HomeAssistant, entry: XiaomiCloudMapExtractorConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def to_configuration(entry: XiaomiCloudMapExtractorConfigEntry) -> XiaomiCloudMapExtractorConnectorConfiguration:
    host = entry.data[CONF_HOST]
    token = entry.data[CONF_TOKEN]
    device_id = entry.data[CONF_DEVICE_ID]
    if device_id is None:
        raise ConfigEntryAuthFailed()
    model = entry.data[CONF_MODEL]
    mac = entry.data[CONF_MAC]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    server = entry.data[CONF_SERVER]
    used_api = VacuumApi.configured(entry.data[CONF_USED_MAP_API], model)

    scale = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_SCALE]
    rotate = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_ROTATE]
    trim_left = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_TRIM_LEFT]
    trim_right = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_TRIM_RIGHT]
    trim_top = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_TRIM_TOP]
    trim_bottom = entry.options[CONF_IMAGE_CONFIG][CONF_IMAGE_CONFIG_TRIM_BOTTOM]
    image_config = ImageConfig(scale, rotate, TrimConfig(trim_left, trim_right, trim_top, trim_bottom))
    fit_to_explored = entry.options[CONF_IMAGE_CONFIG].get(CONF_IMAGE_CONFIG_FIT_TO_EXPLORED, False)

    colors = ColorsPalette({SupportedColor(k): tuple(v) for k, v in entry.options[CONF_COLORS].items()},
                           {k: tuple(v) for k, v in entry.options[CONF_ROOM_COLORS].items()})

    drawables = [Drawable(e) for e in entry.options[CONF_DRAWABLES]]
    sizes = Sizes({Size(k): v for k, v in entry.options[CONF_SIZES].items()})
    texts = []

    config = XiaomiCloudMapExtractorConnectorConfiguration(
        host,
        token,
        username,
        password,
        server,
        used_api,
        device_id,
        mac,
        model,
        image_config,
        colors,
        drawables,
        sizes,
        texts,
        fit_to_explored,
    )
    return config
