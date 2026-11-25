"""The v2 YAML camera platform: imported into a config entry, with a repair saying it is deprecated.

What the YAML may contain is in `legacy_schema.py`.
"""
from typing import Mapping, Any, Callable

import logging
from aiohttp import ClientSession
from homeassistant.config_entries import SOURCE_IMPORT
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType

from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes, Size

from .const import (
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
    DOMAIN,
    NAME,
)
from .connector.vacuums.base.model import VacuumApi
from .connector.xiaomi_cloud.connector import XiaomiCloudConnector, XiaomiCloudDeviceInfo
from .legacy_schema import (
    LEGACY_CONF_BOTTOM,
    LEGACY_CONF_COLORS,
    LEGACY_CONF_COUNTRY,
    LEGACY_CONF_FORCE_API,
    LEGACY_CONF_LEFT,
    LEGACY_CONF_MAP_TRANSFORM,
    LEGACY_CONF_RIGHT,
    LEGACY_CONF_ROTATE,
    LEGACY_CONF_SCALE,
    LEGACY_CONF_SIZES,
    LEGACY_CONF_SIZE_CHARGER_RADIUS,
    LEGACY_CONF_SIZE_IGNORED_OBSTACLE_RADIUS,
    LEGACY_CONF_SIZE_IGNORED_OBSTACLE_WITH_PHOTO_RADIUS,
    LEGACY_CONF_SIZE_MOP_PATH_WIDTH,
    LEGACY_CONF_SIZE_OBSTACLE_RADIUS,
    LEGACY_CONF_SIZE_OBSTACLE_WITH_PHOTO_RADIUS,
    LEGACY_CONF_SIZE_PATH_WIDTH,
    LEGACY_CONF_SIZE_VACUUM_RADIUS,
    LEGACY_CONF_TOP,
    LEGACY_CONF_TRIM,
)

_LOGGER = logging.getLogger(__name__)


def _map_api(force_api: str | None) -> str | None:
    """v2 YAML spells `force_api` in lower case; entries hold the `VacuumApi` value."""
    return VacuumApi(force_api.upper()).value if force_api else None


def handle_old_config(hass: HomeAssistant, config: ConfigType) -> None:
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={**config},
        )
    )

    async_create_issue(
        hass,
        DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": NAME,
        },
    )


async def create_config_entry_data_from_yaml(
    import_info: Mapping[str, Any], session_creator: Callable[[], ClientSession]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    device_id = None
    model = None
    mac = None
    name = None
    server = import_info.get(LEGACY_CONF_COUNTRY, None)
    connector = XiaomiCloudConnector(session_creator)
    try:
        await connector.login_with_credentials(import_info[CONF_USERNAME], import_info[CONF_PASSWORD])
        devices = await connector.get_devices(server)
        device: XiaomiCloudDeviceInfo | None = next(filter(lambda d: d.token == import_info[CONF_TOKEN], devices), None)
        if device is not None:
            device_id = device.device_id
            model = device.model
            mac = format_mac(device.mac)
            name = device.name
            server = device.server
    except Exception as e:  # noqa: BLE001 - the entry is created anyway; setup asks to log in when needed
        _LOGGER.error("Failed to connect to Xiaomi Cloud", exc_info=e)

    data = {
        CONF_HOST: import_info[CONF_HOST],
        CONF_TOKEN: import_info[CONF_TOKEN],
        CONF_DEVICE_ID: device_id,
        CONF_MODEL: model,
        CONF_MAC: mac,
        CONF_NAME: name,
        CONF_USERNAME: import_info[CONF_USERNAME],
        CONF_PASSWORD: import_info[CONF_PASSWORD],
        CONF_SERVER: server,
        CONF_USED_MAP_API: _map_api(import_info.get(LEGACY_CONF_FORCE_API)),
    }
    options = {
        CONF_IMAGE_CONFIG: {
            **default_image_config(),
            CONF_IMAGE_CONFIG_SCALE: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_SCALE],
            CONF_IMAGE_CONFIG_ROTATE: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_ROTATE],
            CONF_IMAGE_CONFIG_TRIM_LEFT: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_TRIM][LEGACY_CONF_LEFT],
            CONF_IMAGE_CONFIG_TRIM_RIGHT: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_TRIM][LEGACY_CONF_RIGHT],
            CONF_IMAGE_CONFIG_TRIM_TOP: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_TRIM][LEGACY_CONF_TOP],
            CONF_IMAGE_CONFIG_TRIM_BOTTOM: import_info[LEGACY_CONF_MAP_TRANSFORM][LEGACY_CONF_TRIM][LEGACY_CONF_BOTTOM],
        },
        CONF_COLORS: {
            **map_colors_map(ColorsPalette.COLORS),
            **map_colors_map(import_info[LEGACY_CONF_COLORS]),
        },
        CONF_ROOM_COLORS: {},
        CONF_DRAWABLES: [
            e.value
            for e in Drawable
            if e not in [
                Drawable.ROOM_NAMES,
                Drawable.NO_CARPET_AREAS,
                Drawable.IGNORED_OBSTACLES,
                Drawable.IGNORED_OBSTACLES_WITH_PHOTO,
            ]
        ],
        CONF_SIZES: {
            **{k.value: v for k, v in Sizes.SIZES.items()},
            Size.VACUUM_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_VACUUM_RADIUS],
            Size.PATH_WIDTH.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_PATH_WIDTH],
            Size.IGNORED_OBSTACLE_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_IGNORED_OBSTACLE_RADIUS],
            Size.IGNORED_OBSTACLE_WITH_PHOTO_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_IGNORED_OBSTACLE_WITH_PHOTO_RADIUS],
            Size.MOP_PATH_WIDTH.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_MOP_PATH_WIDTH],
            Size.OBSTACLE_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_OBSTACLE_RADIUS],
            Size.OBSTACLE_WITH_PHOTO_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_OBSTACLE_WITH_PHOTO_RADIUS],
            Size.CHARGER_RADIUS.value: import_info[LEGACY_CONF_SIZES][LEGACY_CONF_SIZE_CHARGER_RADIUS],
        },
        CONF_TEXTS: [],
    }
    return data, options


def default_image_config() -> dict[str, float]:
    image_config = ImageConfig()
    return {
        CONF_IMAGE_CONFIG_SCALE: image_config.scale,
        CONF_IMAGE_CONFIG_ROTATE: image_config.rotate,
        CONF_IMAGE_CONFIG_TRIM_LEFT: image_config.trim.left,
        CONF_IMAGE_CONFIG_TRIM_RIGHT: image_config.trim.right,
        CONF_IMAGE_CONFIG_TRIM_TOP: image_config.trim.top,
        CONF_IMAGE_CONFIG_TRIM_BOTTOM: image_config.trim.bottom,
    }


def default_colors() -> dict[str, tuple[int, int, int, int]]:
    return map_colors_map(ColorsPalette.COLORS)


def map_colors_map(
    data: dict[str, tuple[int, int, int] | tuple[int, int, int, int]],
) -> dict[str, tuple[int, int, int, int]]:
    return {
        k: ((v[0], v[1], v[2], v[3]) if len(v) == 4 else (v[0], v[1], v[2], 255))
        for k, v in data.items()
    }
