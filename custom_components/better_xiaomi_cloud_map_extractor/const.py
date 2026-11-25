from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

NAME: Final = "Better Xiaomi Cloud Map Extractor"

DOMAIN: Final = "better_xiaomi_cloud_map_extractor"

# The original integration this fork can import setups from (see migration.py).
ORIGINAL_DOMAIN: Final = "xiaomi_cloud_map_extractor"
ORIGINAL_NAME: Final = "Xiaomi Cloud Map Extractor"

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]

CONTENT_TYPE: Final = "image/png"
DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=10)

CONF_USED_MAP_API: Final = "used_map_api"
CONF_SERVER: Final = "server"

CONF_CAPTCHA_CODE: Final = "captcha_code"
CONF_TWO_FACTOR_CODE: Final = "two_factor_code"

CONF_IMAGE_CONFIG: Final = "image_config"
CONF_IMAGE_CONFIG_SCALE: Final = "scale"
CONF_IMAGE_CONFIG_ROTATE: Final = "rotate"
CONF_IMAGE_CONFIG_TRIM_LEFT: Final = "trim_left"
CONF_IMAGE_CONFIG_TRIM_RIGHT: Final = "trim_right"
CONF_IMAGE_CONFIG_TRIM_TOP: Final = "trim_top"
CONF_IMAGE_CONFIG_TRIM_BOTTOM: Final = "trim_bottom"
# Fork-only, Ijai maps: crop to the explored area and zoom (upstream PR #794). Entries written
# before it lack the key, which means off.
CONF_IMAGE_CONFIG_FIT_TO_EXPLORED: Final = "fit_to_explored"

CONF_COLORS: Final = "colors"

CONF_ROOM_COLORS = "room_colors"

CONF_DRAWABLES: Final = "drawables"

CONF_SIZES: Final = "sizes"

CONF_TEXTS: Final = "texts"
CONF_TEXT_VALUE: Final = "text"
CONF_TEXT_X: Final = "x"
CONF_TEXT_Y: Final = "y"
CONF_TEXT_COLOR: Final = "color"
CONF_TEXT_FONT: Final = "font"
CONF_TEXT_FONT_SIZE: Final = "font_size"

STORAGE_VERSION: Final = 1
