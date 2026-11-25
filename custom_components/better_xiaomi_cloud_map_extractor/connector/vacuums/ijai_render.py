"""Fit an Ijai map to its explored area (upstream PR #794, adapted).

Ijai vacuums upload a fixed canvas (800x800 on most models) however little of it is explored, so
the floor plan is drawn as a small island in a large background, with icons and room names to
match. With the "fit to the explored area" option, each map is cropped to its explored pixels plus
a margin and zoomed to about `FIT_TARGET_DIMENSION` pixels on its longer edge. Icons, paths and room
names zoom by the same factor, so the fitted map looks like the full map enlarged.

The trim and scale the user configured are only ever tightened, never loosened. Upstream's bundled
font and transparent background are not taken: room names use Pillow's default font (the same face
as without the option, only larger), and the background keeps the configured "map outside" color.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PIL import Image, ImageFont
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig, TrimConfig
from vacuum_map_parser_base.config.size import Size, Sizes
from vacuum_map_parser_base.config.text import Text
from vacuum_map_parser_base.image_generator import ImageGenerator
from vacuum_map_parser_base.map_data import ImageData, MapData
from vacuum_map_parser_ijai.image_parser import IjaiImageParser
from vacuum_map_parser_ijai.map_data_parser import IjaiMapDataParser
from vacuum_map_parser_ijai.RobotMap_pb2 import RobotMap

# Source pixels kept around the explored area.
FIT_PADDING = 15
# The cropped map is zoomed to at least this many pixels on its longer edge.
FIT_TARGET_DIMENSION = 960
# Size of Pillow's default font, which draws the room names without the option.
DEFAULT_FONT_SIZE = 10

Bounds = tuple[int, int, int, int]


def explored_bounds(map_bytes: bytes, width: int, height: int) -> Bounds | None:
    """(left, bottom, right, top) of the explored pixels, inclusive, in map rows; None if nothing is."""
    if width <= 0 or height <= 0 or len(map_bytes) != width * height:
        return None
    explored = Image.frombytes("L", (width, height), map_bytes).point(
        lambda value: 0 if value == IjaiImageParser.MAP_OUTSIDE else 255
    )
    box = explored.getbbox()
    if box is None:
        return None
    return box[0], box[1], box[2] - 1, box[3] - 1


def fit_image_config(image_config: ImageConfig, width: int, height: int, bounds: Bounds | None) -> ImageConfig:
    """The configured trim and scale, tightened to the explored area."""
    if bounds is None:
        return image_config
    left, bottom, right, top = bounds
    configured = image_config.trim
    # The parser draws map row 0 at the bottom of the image, so trim "bottom" cuts the first rows.
    trim = TrimConfig(
        left=max(configured.left, max(0, left - FIT_PADDING) / width * 100),
        right=max(configured.right, max(0, width - 1 - right - FIT_PADDING) / width * 100),
        top=max(configured.top, max(0, height - 1 - top - FIT_PADDING) / height * 100),
        bottom=max(configured.bottom, max(0, bottom - FIT_PADDING) / height * 100),
    )
    trimmed_width, trimmed_height = _trimmed(trim, width, height)
    if trimmed_width <= 0 or trimmed_height <= 0:
        return image_config
    scale = max(image_config.scale, FIT_TARGET_DIMENSION / max(trimmed_width, trimmed_height))
    return replace(image_config, trim=trim, scale=scale)


def _trimmed(trim: TrimConfig, width: int, height: int) -> tuple[int, int]:
    # As IjaiImageParser.parse rounds the trim.
    return (
        width - int(trim.left * width / 100) - int(trim.right * width / 100),
        height - int(trim.top * height / 100) - int(trim.bottom * height / 100),
    )


def zoomed_sizes(sizes: Sizes, zoom: float) -> Sizes:
    return Sizes({size: sizes.get_size(size) * zoom for size in Size})


class ZoomedImageGenerator(ImageGenerator):
    """Draws room names in Pillow's default font, zoomed with the map."""

    def __init__(self, *args: Any, zoom: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._font = ImageFont.load_default(size=DEFAULT_FONT_SIZE * zoom)

    def _draw_room_names(self, map_data: MapData) -> None:
        if map_data.rooms is None or map_data.image is None:
            return
        color = self._get_color(SupportedColor.ROOM_NAMES)
        for room in map_data.rooms.values():
            point = room.point()
            if point is not None and room.name is not None:
                position = point.to_img(map_data.image.dimensions)
                self._draw_name(map_data.image, room.name, position.x, position.y, color)

    def _draw_name(self, image: ImageData, text: str, x: float, y: float, color: Any) -> None:
        # ImageGenerator._draw_text with a font object; it only takes a font file.
        def draw_func(draw: Any) -> None:
            left, top, right, bottom = draw.textbbox((0, 0), text, self._font)
            draw.text((x - (right - left) / 2, y - (bottom - top) / 2), text, font=self._font, fill=color)

        ImageGenerator._draw_on_new_layer(image, draw_func, ImageGenerator._use_transparency(color))


def fitted_parser(
    decoded_map: bytes,
    palette: ColorsPalette,
    sizes: Sizes,
    drawables: list[Drawable],
    image_config: ImageConfig,
    texts: list[Text],
) -> IjaiMapDataParser | None:
    """A parser that draws this map fitted to its explored area; None when nothing is explored."""
    robot_map = RobotMap()
    robot_map.ParseFromString(decoded_map)
    width, height = robot_map.mapHead.sizeX, robot_map.mapHead.sizeY
    fitted = fit_image_config(image_config, width, height,
                              explored_bounds(robot_map.mapData.mapData, width, height))
    if fitted is image_config:
        return None
    zoom = fitted.scale / image_config.scale
    fitted_sizes = zoomed_sizes(sizes, zoom)
    parser = IjaiMapDataParser(palette, fitted_sizes, drawables, fitted, texts)
    parser._image_generator = ZoomedImageGenerator(  # pylint: disable=protected-access
        palette, fitted_sizes, drawables, fitted, texts, zoom=zoom
    )
    return parser
