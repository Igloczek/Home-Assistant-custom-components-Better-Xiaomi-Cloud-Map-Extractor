"""A map the real parser can't read is a map parse failure for every vendor, never an unknown error.

Each vendor's map goes through its own `vacuum-map-parser-*` package, which raises whatever its
decompression or decoding hits. The connector, the coordinator and Repairs only know
`FailedMapParseException`; anything else reaches the user as "Unexpected error" and makes every
entity unavailable.
"""

from __future__ import annotations

import gzip
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector.platforms import AVAILABLE_VACUUM_PLATFORMS
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedMapParseException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumApi, VacuumConfig

from .conftest import make_device

MODELS = {
    VacuumApi.ROBOROCK: "roborock.vacuum.s5",
    VacuumApi.VIOMI: "viomi.vacuum.v8",
    VacuumApi.ROIDMI: "roidmi.vacuum.v60",
    VacuumApi.DREAME: "dreame.vacuum.p2008",
    VacuumApi.IJAI: "ijai.vacuum.v3",
    VacuumApi.XIAOMI: "xiaomi.vacuum.c102gl",
}
BROKEN_MAPS = {
    "garbage": b"not a map at all",
    "truncated gzip": gzip.compress(b"x" * 4096)[:40],
    "truncated zlib": zlib.compress(b"x" * 4096)[:20],
    "empty": b"",
}


def _vacuum(api: VacuumApi):
    model = MODELS[api]
    config = VacuumConfig(
        connector=MagicMock(),
        device_info=make_device(model=model),
        server="de",
        device_id="123456789",
        host="192.168.1.50",
        token="0123456789abcdef0123456789abcdef",
        model=model,
        palette=ColorsPalette(),
        drawables=[],
        image_config=ImageConfig(),
        sizes=Sizes(),
        texts=[],
    )
    vacuum = AVAILABLE_VACUUM_PLATFORMS[api](config)
    if hasattr(vacuum, "_miot_device"):
        # Xiaomi models read their state from the vacuum on the local network; none here.
        from miio.exceptions import DeviceException

        vacuum._miot_device = MagicMock(send=MagicMock(side_effect=DeviceException("no vacuum")))
    if api is VacuumApi.IJAI:
        vacuum._wifi_info_sn = "0123456789AB"  # read from the vacuum before the first map
    return vacuum


@pytest.mark.parametrize("raw_map", BROKEN_MAPS.values(), ids=BROKEN_MAPS.keys())
@pytest.mark.parametrize("api", MODELS, ids=[api.value for api in MODELS])
async def test_broken_map_is_a_parse_failure(api: VacuumApi, raw_map: bytes) -> None:
    vacuum = _vacuum(api)

    with (
        patch.object(type(vacuum), "get_map_name", AsyncMock(return_value="map_0")),
        patch.object(type(vacuum), "get_raw_map_data", AsyncMock(return_value=raw_map)),
        pytest.raises(FailedMapParseException),
    ):
        await vacuum.get_map()


async def test_parser_returning_nothing_is_a_parse_failure() -> None:
    vacuum = _vacuum(VacuumApi.ROBOROCK)

    with (
        patch.object(type(vacuum), "get_map_name", AsyncMock(return_value="map_0")),
        patch.object(type(vacuum), "get_raw_map_data", AsyncMock(return_value=b"x")),
        patch.object(type(vacuum), "decode_and_parse", MagicMock(return_value=None)),
        pytest.raises(FailedMapParseException),
    ):
        await vacuum.get_map()


@pytest.mark.parametrize("api", MODELS, ids=[api.value for api in MODELS])
async def test_parser_failing_after_the_map_unpacked_is_a_parse_failure(api: VacuumApi) -> None:
    """The map decompresses or decrypts, then the parser hits something it does not expect."""
    import custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai as ijai

    vacuum = _vacuum(api)
    parser = type(vacuum.map_data_parser)
    with (
        patch.object(type(vacuum), "get_map_name", AsyncMock(return_value="map_0")),
        patch.object(type(vacuum), "get_raw_map_data", AsyncMock(return_value=b"x")),
        patch.object(parser, "unpack_map", MagicMock(return_value=b"unpacked"), create=True),
        patch.object(ijai, "unpack_map", MagicMock(return_value=b"unpacked")),
        patch.object(ijai, "_with_current_map_info", MagicMock(side_effect=lambda m: m)),
        patch.object(parser, "parse", MagicMock(side_effect=KeyError("rooms"))),
        pytest.raises(FailedMapParseException),
    ):
        await vacuum.get_map()
