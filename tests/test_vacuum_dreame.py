"""Dreame map decoding with the real vacuum-map-parser-dreame.

Maps are built by tests/dreame_map.py the way the vacuums upload them. Upstream #450, #757 and
#772: a dreame.vacuum.md1808 serves an AES-encrypted map, which the integration fed straight into
zlib ("incorrect header check", shown as an unknown error).
"""

from __future__ import annotations

import json

import base64
import logging
import zlib
from unittest.mock import MagicMock

from unittest.mock import AsyncMock

import pytest
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_dreame.map_data_parser import DreameMapDataParser

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    EncryptedMapUnsupportedException,
    FailedMapParseException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_dreame import DreameCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import (
    XiaomiCloudConnector,
    XiaomiCloudDeviceInfo,
)

from .const import TOKEN
from .dreame_map import MD1808_ENCRYPTED_MAP, build_frame, encrypted_map, plain_map
from .log_secrets import assert_no_secrets

DEVICE_ID = "100000001"
MD1808 = "dreame.vacuum.md1808"
ENCRYPTED_MODEL = "dreame.vacuum.r2209"
MAP_KEY = "made-up-dreame-map-key"


def dreame(model: str, enc_key: str | None = None) -> DreameCloudVacuum:
    device_info = XiaomiCloudDeviceInfo(
        device_id=DEVICE_ID, name="Vacuum", model=model, token=TOKEN, spec_type="", local_ip="192.0.2.10",
        mac="aa:bb:cc:dd:ee:01", server="de", home_id=1, user_id=1,
    )
    config = VacuumConfig(
        MagicMock(spec=XiaomiCloudConnector), device_info, "de", DEVICE_ID, "192.0.2.10", TOKEN, model,
        ColorsPalette(), [], ImageConfig(), Sizes({}), [],
    )
    vacuum = DreameCloudVacuum(config)
    vacuum._enc_key = enc_key
    return vacuum


def test_unencrypted_map_is_drawn() -> None:
    data = dreame("dreame.vacuum.p2008").decode_and_parse(plain_map(build_frame(width=3, height=2)))

    assert (data.image.dimensions.width, data.image.dimensions.height) == (3, 2)


def test_encrypted_map_with_its_key_is_drawn() -> None:
    raw = encrypted_map(build_frame(), MAP_KEY, DreameMapDataParser.IVs[ENCRYPTED_MODEL])

    data = dreame(ENCRYPTED_MODEL, MAP_KEY).decode_and_parse(raw)

    assert data.image is not None


def test_md1808_fixture_has_the_shape_of_the_reported_map() -> None:
    """The fixture stands in for the map attached to upstream #450: standard base64, whole AES blocks,
    no zlib header."""
    text = MD1808_ENCRYPTED_MAP.read_bytes()
    raw = base64.b64decode(text, validate=True)

    assert b"\n" not in text and len(raw) % 16 == 0 and raw[0] != 0x78
    with pytest.raises(zlib.error, match="incorrect header check"):
        zlib.decompress(raw)


def test_md1808_encrypted_map_says_it_cannot_be_decrypted(caplog: pytest.LogCaptureFixture) -> None:
    """Upstream #450: "incorrect header check" became an unknown error. The failure now says the map is
    encrypted and names the model, and logs no map content."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EncryptedMapUnsupportedException) as failure:
        dreame(MD1808).decode_and_parse(MD1808_ENCRYPTED_MAP.read_bytes())

    assert failure.value.model == MD1808
    assert MD1808 in str(failure.value) and "encrypted" in str(failure.value)
    assert_no_secrets(caplog, {"map key": "made-up-md1808-map-key", "map": MD1808_ENCRYPTED_MAP.read_text()[:24]},
                      str(failure.value))


def test_encrypted_model_with_the_wrong_key_is_a_parse_failure() -> None:
    """A model whose IV is known got a key that does not open the map: not "cannot decrypt this model"."""
    raw = encrypted_map(build_frame(), MAP_KEY, DreameMapDataParser.IVs[ENCRYPTED_MODEL])

    with pytest.raises(FailedMapParseException) as failure:
        dreame(ENCRYPTED_MODEL, "another-made-up-key").decode_and_parse(raw)

    assert not isinstance(failure.value, EncryptedMapUnsupportedException)


BROKEN_MAPS = {
    "not base64": b"not*base64*at*all!",
    "truncated zlib": plain_map(build_frame())[:12],
    "P-frame": plain_map(build_frame()[:4] + bytes([80]) + build_frame()[5:]),
    "too short for a header": plain_map(b"\x00" * 10),
    "image without its JSON": plain_map(build_frame()[:27 + 4] + b"{not json"),
}


@pytest.mark.parametrize("raw", BROKEN_MAPS.values(), ids=BROKEN_MAPS.keys())
def test_unreadable_map_is_a_parse_failure(raw: bytes) -> None:
    """Every map the parser cannot read is a map parse failure ("retried on the next update"), never an
    unknown error from inside the parser."""
    with pytest.raises(FailedMapParseException):
        dreame("dreame.vacuum.p2008").decode_and_parse(raw)


async def test_map_name_request_after_an_answer_without_a_time_stays_json() -> None:
    """The vacuum answers with no map and no time: the next request still sends valid JSON."""
    vacuum = dreame(ENCRYPTED_MODEL)
    vacuum._connector.get_other_info = AsyncMock(return_value={"result": {"out": [{"value": ""}]}})

    assert await vacuum.get_map_name() is None
    assert await vacuum.get_map_name() is None

    sent = vacuum._connector.get_other_info.call_args.kwargs["parameters"]["in"][0]["value"]
    json.loads(sent)
