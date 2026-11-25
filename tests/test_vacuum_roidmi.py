"""Roidmi map download and parsing, including roidmi.vacuum.sdj60 (upstream #572, PR #768).

The maps are synthetic (`tests/roidmi_map.py`); see there for the two layouts.
"""

from __future__ import annotations

import gzip
from unittest.mock import AsyncMock, MagicMock

import pytest
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import (
    VacuumApi,
    VacuumConfig,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_roidmi import RoidmiCloudVacuum

from .conftest import make_device
from .roidmi_map import (
    HEIGHT,
    SDJ60_INTERIM_FILE,
    UNKNOWN,
    WIDTH,
    roidmi_map,
    sdj60_map,
)

DEVICE_ID = "123456789"
USER_ID = 2
API_URL = "https://de.api.io.mi.com/app"
INTERIM_URL = "https://example.invalid/interim"
PRO_URL = "https://example.invalid/pro"
SDJ60 = "roidmi.vacuum.sdj60"


def make_vacuum(model: str, files: dict[str, bytes], urls: dict[str, str | None]) -> tuple[RoidmiCloudVacuum, MagicMock]:
    """A Roidmi vacuum whose cloud answers each map URL endpoint with `urls` and serves `files`."""
    connector = MagicMock()
    connector.get_api_url = MagicMock(return_value=API_URL)

    async def api_call(url: str, _params: dict[str, str]) -> dict:
        endpoint = url.removeprefix(API_URL)
        return {"result": {"url": urls[endpoint]}} if urls.get(endpoint) else {"code": 0, "result": None}

    connector.execute_api_call_encrypted = AsyncMock(side_effect=api_call)
    connector.get_raw_map_data = AsyncMock(side_effect=lambda url: files.get(url))
    config = VacuumConfig(
        connector=connector,
        device_info=make_device(model=model, device_id=DEVICE_ID, user_id=USER_ID),
        server="de",
        device_id=DEVICE_ID,
        host="192.168.1.50",
        token="0123456789abcdef0123456789abcdef",
        model=model,
        palette=ColorsPalette(),
        drawables=[],
        image_config=ImageConfig(),
        sizes=Sizes(),
        texts=[],
    )
    return RoidmiCloudVacuum(config), connector


BOTH_ENDPOINTS = {"/v2/home/get_interim_file_url": INTERIM_URL, "/v2/home/get_interim_file_url_pro": PRO_URL}


def _endpoints(connector: MagicMock) -> list[str]:
    return [call.args[0].removeprefix(API_URL) for call in connector.execute_api_call_encrypted.await_args_list]


def _assert_rooms(map_data) -> None:
    assert map_data.image.dimensions.width == WIDTH
    assert map_data.image.dimensions.height == HEIGHT
    rooms = {room.name: (room.x0, room.y0, room.x1, room.y1) for room in map_data.rooms.values()}
    # Cell corners in map units (mm): cell * resolution (0.05 m) * 1000.
    assert rooms == {"Kitchen": pytest.approx((0, 0, 50, 50)), "Hall": pytest.approx((100, 0, 150, 100))}
    assert (map_data.charger.x, map_data.charger.y) == (100, 150)


def test_sdj60_is_a_roidmi_model() -> None:
    assert VacuumApi.detect(SDJ60) is VacuumApi.ROIDMI


async def test_sdj60_map_comes_from_the_pro_endpoint() -> None:
    """The Mi Home app reads the sdj60 map from `get_interim_file_url_pro`; the other endpoint still
    answers with a URL, to a file that is no map (upstream #572)."""
    files = {INTERIM_URL: SDJ60_INTERIM_FILE, PRO_URL: sdj60_map()}
    vacuum, connector = make_vacuum(SDJ60, files, BOTH_ENDPOINTS)

    map_data, raw = await vacuum.get_map()

    assert _endpoints(connector)[0] == "/v2/home/get_interim_file_url_pro"
    assert raw == files[PRO_URL]
    _assert_rooms(map_data)
    assert vacuum.additional_data()["last_used_url"] == API_URL + "/v2/home/get_interim_file_url_pro"


async def test_sdj60_falls_back_to_the_standard_endpoint() -> None:
    """If the pro endpoint has no map, the standard one is asked, as for every other Roidmi."""
    files = {INTERIM_URL: sdj60_map()}
    vacuum, connector = make_vacuum(SDJ60, files, {"/v2/home/get_interim_file_url": INTERIM_URL})

    map_data, _ = await vacuum.get_map()

    assert _endpoints(connector) == ["/v2/home/get_interim_file_url_pro", "/v2/home/get_interim_file_url"]
    _assert_rooms(map_data)


@pytest.mark.parametrize(
    ("header", "last_cell"),
    [
        (b"\x01" * 20, 0),  # neither the 16-byte header nor the outside cell at the end
        (b"\x01" * 16, 0),  # the bitmap does not end with an outside cell
        (b"\x01" * 20, 127),  # the header is not 16 bytes
        (b"\x01" * 16, 127),  # what the v60 layout looks like also works
        (b"", UNKNOWN),
    ],
    ids=["header20-wall", "header16-wall", "header20-outside", "header16-outside", "noheader-unknown"],
)
async def test_sdj60_bitmap_is_the_cells_right_before_the_json(header: bytes, last_cell: int) -> None:
    files = {PRO_URL: sdj60_map(header=header, last_cell=last_cell)}
    vacuum, _ = make_vacuum(SDJ60, files, BOTH_ENDPOINTS)

    map_data, _ = await vacuum.get_map()

    _assert_rooms(map_data)


@pytest.mark.parametrize("model", ["roidmi.vacuum.v60", "viomi.vacuum.v18", "zhimi.vacuum.a1"])
async def test_other_roidmi_models_are_unchanged(model: str) -> None:
    """Every other Roidmi-format model asks the standard endpoint first and uses the parser's split."""
    files = {INTERIM_URL: roidmi_map(), PRO_URL: b"not this one"}
    vacuum, connector = make_vacuum(model, files, BOTH_ENDPOINTS)

    map_data, _ = await vacuum.get_map()

    assert _endpoints(connector) == ["/v2/home/get_interim_file_url"]
    _assert_rooms(map_data)
    assert type(vacuum.map_data_parser).__name__ == "RoidmiMapDataParser"


async def test_sdj60_without_a_map_asks_each_endpoint_once() -> None:
    vacuum, connector = make_vacuum(SDJ60, {}, {})

    assert await vacuum.get_map_url("0") is None
    assert _endpoints(connector) == ["/v2/home/get_interim_file_url_pro", "/v2/home/get_interim_file_url"]


async def test_sdj60_map_shorter_than_its_bitmap_fails_to_parse() -> None:
    """A truncated map is a parse failure, not a bitmap cut from the wrong bytes."""
    files = {PRO_URL: gzip.compress(b'\x00\x00{"width": 6, "height": 5}')}
    vacuum, _ = make_vacuum(SDJ60, files, BOTH_ENDPOINTS)

    with pytest.raises(ValueError):
        await vacuum.get_map()
