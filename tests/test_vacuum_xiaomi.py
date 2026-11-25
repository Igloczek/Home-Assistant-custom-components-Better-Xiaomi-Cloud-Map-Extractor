"""Xiaomi-format (MIoT, Shunzao family) map download and decryption.

The cloud object is `{"version": 2, "data": base64(AES-CBC(zlib(map JSON)))}`. The AES key is
derived the way the Xiaomi Home map plugin does it (upstream #704, #762 and
vacuum-map-parser-xiaomi PR #5, read from the app bundle): the model key is
`Device.model.slice(-16)`, and

    key = MD5(AES-128-CBC(model_key + did, key=model_key, iv="ABCDEF1234123412", PKCS7))

`encrypt_xiaomi_map` implements that recipe on its own, so the tests do not share the code
under test. `tests/fixtures/xiaomi/map.json` is a synthetic map, not taken from any device.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from pathlib import Path
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_xiaomi.aes_decryptor import gen_md5_key

from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedMapParseException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_xiaomi import (
    XiaomiCloudVacuum,
)

from .conftest import make_device
from .fake_miio import FakeLanVacuum, lan_vacuum

MAP_JSON = (Path(__file__).parent / "fixtures" / "xiaomi" / "map.json").read_text()
DEVICE_ID = "123456789"
USER_ID = 2
IV = b"ABCDEF1234123412"
MAP_URL = "https://example.invalid/map"

# Model names from upstream reports. The ones that are not 20 characters long
# ("xiaomi." + 6-character suffix) failed with "Incorrect AES key length".
MODELS = [
    "xiaomi.vacuum.ov81gl",  # 20 chars, worked before (upstream #704)
    "xiaomi.vacuum.ov51gl",  # 20 chars, worked before (upstream #762)
    "xiaomi.vacuum.c107",  # 18 chars, Mijia M30 Pro (upstream #732)
    "xiaomi.vacuum.d101",  # 18 chars, H40 (upstream #733)
    "xiaomi.vacuum.c101eu",  # 20 chars, listed in _NON_STANDARD_MAP_PROP
    "xiaomi.vacuum.b112",  # 18 chars, listed in _NON_STANDARD_MAP_PROP
]


def app_key(model: str, device_id: str) -> bytes:
    model_key = model[-16:].encode()
    wrapped = AES.new(model_key, AES.MODE_CBC, IV).encrypt(pad(model_key + device_id.encode(), 16))
    return hashlib.md5(wrapped).digest()


def encrypt_xiaomi_map(map_json: str, model: str, device_id: str) -> bytes:
    """Build the cloud object the vacuum uploads, as the Xiaomi Home app reads it."""
    cipher = AES.new(app_key(model, device_id), AES.MODE_CBC, IV)
    data = cipher.encrypt(pad(zlib.compress(map_json.encode()), 16))
    return json.dumps({"version": 2, "data": base64.b64encode(data).decode()}).encode()


def make_vacuum(model: str, raw_map: bytes) -> tuple[XiaomiCloudVacuum, MagicMock]:
    connector = MagicMock()
    connector.get_api_url = MagicMock(return_value="https://de.api.io.mi.com/app")
    connector.execute_api_call_encrypted = AsyncMock(return_value={"result": {"url": MAP_URL}})
    connector.get_raw_map_data = AsyncMock(return_value=raw_map)
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
    return XiaomiCloudVacuum(config), connector


@pytest.fixture
def miot_device() -> Iterator[FakeLanVacuum]:
    """The vacuum on the LAN, reporting the live map object in MIoT property 10/1."""
    lan = FakeLanVacuum(
        "xiaomi.vacuum.ov81gl", properties={(10, 1): json.dumps({"obj_name": f"{USER_ID}/{DEVICE_ID}/0"})}
    )
    with lan_vacuum(lan):
        yield lan


@pytest.mark.parametrize("model", MODELS)
async def test_xiaomi_map_decrypts_for_every_model_length(model: str, miot_device: FakeLanVacuum) -> None:
    """The map of every Xiaomi model downloads, decrypts and parses."""
    raw_map = encrypt_xiaomi_map(MAP_JSON, model, DEVICE_ID)
    vacuum, connector = make_vacuum(model, raw_map)

    map_data, returned_raw = await vacuum.get_map()

    connector.get_raw_map_data.assert_awaited_once_with(MAP_URL)
    assert returned_raw == raw_map
    assert map_data.map_name == "0"
    assert map_data.image.dimensions.width == 10
    assert map_data.image.dimensions.height == 8
    assert {room.name for room in map_data.rooms.values()} == {"Kitchen", "Hall"}


@pytest.mark.parametrize("model", MODELS)
def test_xiaomi_enc_key_matches_the_app(model: str, miot_device: FakeLanVacuum) -> None:
    """The exported `enc_key` attribute is the key the map is encrypted with."""
    vacuum, _ = make_vacuum(model, b"")

    assert vacuum.additional_data()["enc_key"] == app_key(model, DEVICE_ID).hex()


# Every Xiaomi-format model id known to this integration, vacuum-map-parser-xiaomi (code and
# README) and the upstream reports (#704, #720, #732, #733, #748, #762, #764).
KNOWN_XIAOMI_MODELS = [
    "xiaomi.vacuum.b106bk", "xiaomi.vacuum.b106eu", "xiaomi.vacuum.b106tr", "xiaomi.vacuum.b108gl",
    "xiaomi.vacuum.b108gp", "xiaomi.vacuum.b112", "xiaomi.vacuum.b112bk", "xiaomi.vacuum.b112gl",
    "xiaomi.vacuum.b112tr", "xiaomi.vacuum.c101", "xiaomi.vacuum.c101eu", "xiaomi.vacuum.c102",
    "xiaomi.vacuum.c103", "xiaomi.vacuum.c104", "xiaomi.vacuum.c107", "xiaomi.vacuum.d101",
    "xiaomi.vacuum.d106gl", "xiaomi.vacuum.d109gl", "xiaomi.vacuum.e101gb", "xiaomi.vacuum.e101gl",
    "xiaomi.vacuum.ov21gl", "xiaomi.vacuum.ov31gl", "xiaomi.vacuum.ov32gl", "xiaomi.vacuum.ov42gl",
    "xiaomi.vacuum.ov43gb", "xiaomi.vacuum.ov43gl", "xiaomi.vacuum.ov51", "xiaomi.vacuum.ov51gl",
    "xiaomi.vacuum.ov71gl", "xiaomi.vacuum.ov81", "xiaomi.vacuum.ov81gl",
]
AES_KEY_LENGTHS = (16, 24, 32)


def old_model_key(model: str) -> str:
    """The key upstream v3.0.0-alpha-24 (688fe3e) used."""
    return model.replace("xiaomi", "mi")


@pytest.mark.parametrize(
    "model", [m for m in KNOWN_XIAOMI_MODELS if len(old_model_key(m)) in AES_KEY_LENGTHS]
)
def test_models_that_worked_keep_the_same_key(model: str, miot_device: FakeLanVacuum) -> None:
    """No vacuum whose map decrypted before gets a different key: the key bytes are identical."""
    vacuum, _ = make_vacuum(model, b"")

    assert vacuum.additional_data()["enc_key"] == gen_md5_key(old_model_key(model), DEVICE_ID)


def test_every_known_model_had_either_a_16_byte_key_or_none() -> None:
    """The old key was valid only at 16 bytes for known models, so the test above covers all of them."""
    lengths = {len(old_model_key(m)) for m in KNOWN_XIAOMI_MODELS}
    assert lengths & set(AES_KEY_LENGTHS) == {16}
    assert all(len(m[-16:]) == 16 for m in KNOWN_XIAOMI_MODELS)


ROBOROCK_LIKE_MAP = b"\x1f\x8b\x08\x00" + bytes(range(256)) * 4  # a gzip map, not a Xiaomi one


@pytest.mark.parametrize("model", ["roborock.vacuum.s5e", "rockrobo.vacuum.v1", "viomi.vacuum.v8"])
async def test_map_that_is_not_in_xiaomi_format_fails_as_a_parse_error(model: str, miot_device: FakeLanVacuum) -> None:
    """Another vendor's map is tried, and when it does not decrypt that is a map parsing failure.

    The coordinator offers the matching format only for this error, so a crypto or zlib error
    must not escape as something else. `viomi.vacuum.v8` is shorter than an AES key.
    """
    vacuum, connector = make_vacuum(model, ROBOROCK_LIKE_MAP)

    with pytest.raises(FailedMapParseException):
        await vacuum.get_map()

    connector.get_raw_map_data.assert_awaited_once()
    assert "enc_key" in vacuum.additional_data()


async def test_other_vendor_whose_map_is_in_xiaomi_format_still_works(miot_device: FakeLanVacuum) -> None:
    """Nothing is refused by model name: if the Xiaomi format decodes, the map shows."""
    model = "roborock.vacuum.a15"
    vacuum, _ = make_vacuum(model, encrypt_xiaomi_map(MAP_JSON, model, DEVICE_ID))

    map_data, _ = await vacuum.get_map()

    assert map_data.image.dimensions.width == 10
