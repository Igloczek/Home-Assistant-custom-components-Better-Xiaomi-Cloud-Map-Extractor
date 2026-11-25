"""Jonr P20 (`xtl.vacuum.xm2216`, upstream #621) map download and parsing.

The maps are synthetic (`tests/xtl_map.py` documents the format). The marker positions follow
the frame Maedrawd's fork fitted to real captures and three owners confirmed against the app.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.image_config import ImageConfig, TrimConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector.platforms import AVAILABLE_VACUUM_PLATFORMS
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import (
    VacuumApi,
    VacuumConfig,
)

from .conftest import make_device
from .xtl_map import (
    CHARGER,
    FLOOR,
    HEIGHT,
    KITCHEN,
    LIVING,
    TRACE,
    UNEXPLORED,
    VACUUM,
    WALL,
    WIDTH,
    grid,
    lz4_block,
    to_pixel,
    trace_field,
    xtl_map,
)

MODEL = "xtl.vacuum.xm2216"
DEVICE_ID = "123456789"
API_URL = "https://de.api.io.mi.com/app"
MAP_URL = "https://example.invalid/map"


def make_vacuum(raw_map: bytes, image_config: ImageConfig | None = None):
    connector = MagicMock()
    connector.get_api_url = MagicMock(return_value=API_URL)
    connector.execute_api_call_encrypted = AsyncMock(return_value={"result": {"url": MAP_URL}})
    connector.get_raw_map_data = AsyncMock(return_value=raw_map)
    config = VacuumConfig(
        connector=connector,
        device_info=make_device(model=MODEL, device_id=DEVICE_ID, user_id=2),
        server="de",
        device_id=DEVICE_ID,
        host="192.168.1.50",
        token="0123456789abcdef0123456789abcdef",
        model=MODEL,
        palette=ColorsPalette(),
        drawables=[],
        image_config=image_config or ImageConfig(),
        sizes=Sizes(),
        texts=[],
    )
    return AVAILABLE_VACUUM_PLATFORMS[VacuumApi.detect(MODEL)](config), connector


def _rgba(color) -> tuple:
    return tuple(color) + (255,) if len(color) == 3 else tuple(color)


def test_jonr_p20_has_its_own_map_format() -> None:
    assert VacuumApi.detect(MODEL) == "XTL"
    assert VacuumApi("XTL").title() == "Xtl"


def test_other_xtl_models_stay_unsupported() -> None:
    """Only the P20's format is known; another model shows "not supported" and offers diagnostics."""
    assert VacuumApi.detect("xtl.vacuum.xm2217") is VacuumApi.UNSUPPORTED


async def test_map_downloads_and_parses() -> None:
    raw = xtl_map()
    vacuum, connector = make_vacuum(raw)

    map_data, returned = await vacuum.get_map()

    assert returned == raw
    connector.get_raw_map_data.assert_awaited_once_with(MAP_URL)
    assert vacuum.additional_data()["last_used_url"] == API_URL + "/v2/home/get_interim_file_url"
    assert map_data.map_name == "0"
    assert (map_data.image.dimensions.width, map_data.image.dimensions.height) == (WIDTH, HEIGHT)
    assert map_data.image.data.size == (WIDTH, HEIGHT)


async def test_cells_are_drawn_in_their_colors_with_row_0_at_the_bottom() -> None:
    palette = ColorsPalette()
    vacuum, _ = make_vacuum(xtl_map())
    map_data, _ = await vacuum.get_map()
    pixels = map_data.image.data.load()
    cells = grid()

    def at(column: int, row: int) -> tuple:
        return pixels[column, HEIGHT - 1 - row]

    assert cells[5][3] == FLOOR and at(3, 5) == _rgba(palette.get_color(SupportedColor.MAP_INSIDE))
    assert cells[0][3] == WALL and at(3, 0) == _rgba(palette.get_color(SupportedColor.MAP_WALL_V2))
    assert cells[28][3] == UNEXPLORED and at(3, 28) == _rgba(palette.get_color(SupportedColor.MAP_OUTSIDE))
    assert at(10, 10) == _rgba(palette.get_room_color(LIVING))
    assert at(30, 10) == _rgba(palette.get_room_color(KITCHEN)), "room ids above 8 are rooms too"


async def test_charger_and_vacuum_land_on_their_cells() -> None:
    vacuum, _ = make_vacuum(xtl_map())
    map_data, _ = await vacuum.get_map()
    dimensions = map_data.image.dimensions

    charger = map_data.charger.to_img(dimensions)
    position = map_data.vacuum_position.to_img(dimensions)

    assert (charger.x, charger.y) == to_pixel(CHARGER["x"], CHARGER["y"])
    assert (position.x, position.y) == to_pixel(VACUUM["x"], VACUUM["y"])
    # Headings are centi-degrees; the drawing library wants degrees.
    assert map_data.charger.a == 180
    assert map_data.vacuum_position.a == 90


@pytest.mark.parametrize("scale", [1, 2, 3])
async def test_markers_stay_on_the_map_at_any_scale(scale: int) -> None:
    vacuum, _ = make_vacuum(xtl_map(), ImageConfig(scale=scale))
    map_data, _ = await vacuum.get_map()

    assert map_data.image.data.size == (WIDTH * scale, HEIGHT * scale)
    charger = map_data.charger.to_img(map_data.image.dimensions)
    x, y = to_pixel(CHARGER["x"], CHARGER["y"])
    assert (charger.x, charger.y) == (x * scale, y * scale)


async def test_trim_crops_the_picture_like_the_markers() -> None:
    trim = TrimConfig(left=10, right=0, top=0, bottom=20)  # 4 columns, 6 rows
    vacuum, _ = make_vacuum(xtl_map(), ImageConfig(trim=trim))
    map_data, _ = await vacuum.get_map()

    assert map_data.image.data.size == (WIDTH - 4, HEIGHT - 6)
    charger = map_data.charger.to_img(map_data.image.dimensions)
    x, y = to_pixel(CHARGER["x"], CHARGER["y"])
    assert (charger.x, charger.y) == (x - 4, y)


async def test_path_comes_from_the_trace() -> None:
    vacuum, _ = make_vacuum(xtl_map())
    map_data, _ = await vacuum.get_map()

    assert [[(p.x, p.y) for p in segment] for segment in map_data.path.path] == [[(x, y) for x, y, _ in TRACE]]
    assert map_data.path.path[0][2].a == 45
    first = map_data.path.path[0][0].to_img(map_data.image.dimensions)
    assert (first.x, first.y) == to_pixel(TRACE[0][0], TRACE[0][1])


async def test_a_jump_in_the_trace_starts_a_new_segment() -> None:
    """A gap is not drawn as a straight line across the flat."""
    points = [(390, 480, 0), (391, 481, 0), (410, 495, 0), (411, 495, 0)]
    vacuum, _ = make_vacuum(xtl_map(trace=trace_field(points)))
    map_data, _ = await vacuum.get_map()

    assert [[(p.x, p.y) for p in segment] for segment in map_data.path.path] == [
        [(390, 480), (391, 481)], [(410, 495), (411, 495)]
    ]


@pytest.mark.parametrize("trace", ["", "not base64 at all", trace_field([])], ids=["empty", "garbage", "no-points"])
async def test_a_bad_trace_costs_the_path_not_the_map(trace: str) -> None:
    vacuum, _ = make_vacuum(xtl_map(trace=trace))
    map_data, _ = await vacuum.get_map()

    assert map_data.path is None
    assert map_data.charger is not None


async def test_a_position_off_the_map_is_logged_at_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    """The vacuum can report a position outside the map window while it explores; that must not put a
    warning in the log at every update."""
    far = {"x": VACUUM["x"] + 500, "y": VACUUM["y"], "a": 0, "i": 0}
    vacuum, _ = make_vacuum(xtl_map(position=far))

    await vacuum.get_map()

    assert not [r for r in caplog.records if r.levelname not in ("DEBUG", "INFO")]


async def test_a_corrupt_map_fails_to_parse() -> None:
    outer = json.loads(xtl_map())
    vacuum, _ = make_vacuum(json.dumps({**outer, "fields": ["eyJ3aWR0aCI6IDF9"]}).encode())

    with pytest.raises((KeyError, ValueError)):
        await vacuum.get_map()


def test_lz4_runs_longer_than_a_length_byte() -> None:
    """A long run of unexplored cells (a 800 x 800 canvas has many) needs extra length bytes."""
    from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.map_parser_xtl import (
        lz4_decompress_block,
    )

    data = bytes([0] * 1000 + [1, 2, 3] * 10 + [5] * 300 + list(range(20)))
    assert lz4_decompress_block(lz4_block(data), len(data)) == data
    with pytest.raises(ValueError):
        lz4_decompress_block(lz4_block(data), len(data) + 1)
