"""Build encrypted Ijai maps the way the vacuum uploads them.

No reporter attached a real map, so the map is built with the parser's own protobuf schema
(`RobotMap.proto`) and encrypted like the Mi Home plugin does: AES-ECB over the hex string of
the zlib-compressed protobuf. The key derivation is written out here rather than taken from the
parser, so a parser that derives the wrong key fails these tests.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any

import base64

from Crypto.Cipher import AES
from Crypto.Hash import MD5
from Crypto.Util.Padding import pad
from vacuum_map_parser_ijai.RobotMap_pb2 import RobotMap

FIXTURES = Path(__file__).parent / "fixtures" / "ijai"

# Made-up identity of the test vacuum.
WIFI_SN = "ABCDEF123456GHIJKL"
OWNER_ID = "1234567890"
LOGGED_IN_ID = "9876543210"
DEVICE_ID = "123456789"
MAC = "AA:BB:CC:DD:EE:FF"

# Models whose map key is the full MD5 digest as 16 bytes (Mi Home plugin "isEncryptKeyTypeHex",
# upstream PR #725); the others use the middle 16 hex digits, upper-cased, as the key.
HEX_KEY_MODELS = {"v1", "v2", "v3", "v13", "v15", "v17", "v18", "v19", "sz02",
                  "c101", "c103", "c104", "c101eu", "b106eu", "b106tr"}

ROOM = 10  # first room id the parser accepts
CLEANED_ROOM = 60  # room 10, marked as cleaned


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def build_map(map_id: int = 1, listed_maps: list[int] | None = None) -> bytes:
    """A 4x4 map: wall, one cleaned room and one room named "Kitchen"."""
    robot_map = RobotMap()
    robot_map.mapType = 1
    head = robot_map.mapHead
    head.mapHeadId = map_id
    head.sizeX = head.sizeY = 4
    head.minX, head.minY, head.maxX, head.maxY = -2.0, -2.0, 2.0, 2.0
    head.resolution = 1.0
    robot_map.mapData.mapData = bytes(
        [0xFF, 0xFF, 0xFF, 0xFF,
         0xFF, ROOM, CLEANED_ROOM + 1, 0xFF,
         0xFF, ROOM, ROOM + 1, 0xFF,
         0xFF, 0xFF, 0xFF, 0xFF]
    )
    for listed in [map_id] if listed_maps is None else listed_maps:
        robot_map.mapInfo.add(mapHeadId=listed, mapName=f"Floor {listed}")
    room = robot_map.roomDataInfo.add(roomId=ROOM + 1, roomName="Kitchen")
    room.roomNamePost.x, room.roomNamePost.y = 0.5, 0.5
    robot_map.chargeStation.x, robot_map.chargeStation.y = 0.0, 0.0
    return robot_map.SerializeToString()


# A large canvas with a small explored island, as Ijai vacuums upload it: the canvas (800x800 on
# real vacuums) does not shrink to what was explored. 20 pixels per metre, origin at the corner.
CANVAS = 200
ISLAND = (60, 100, 99, 119)  # left, bottom row, right, top row of the explored pixels, inclusive
OUTSIDE = 0x00
WALL = 0xFF


def island_pixel_to_map(x: float, y: float) -> tuple[float, float]:
    return x / 20, y / 20


def build_island_map(island: tuple[int, int, int, int] | None = ISLAND) -> bytes:
    """A CANVAS x CANVAS map, unexplored except a walled room "Kitchen" with the charger in it."""
    robot_map = RobotMap()
    robot_map.mapType = 1
    head = robot_map.mapHead
    head.mapHeadId = 1
    head.sizeX = head.sizeY = CANVAS
    head.minX, head.minY, head.maxX, head.maxY = 0.0, 0.0, CANVAS / 20, CANVAS / 20
    head.resolution = 0.05
    pixels = bytearray([OUTSIDE] * CANVAS * CANVAS)
    if island is not None:
        left, bottom, right, top = island
        for y in range(bottom, top + 1):
            for x in range(left, right + 1):
                edge = x in (left, right) or y in (bottom, top)
                pixels[y * CANVAS + x] = WALL if edge else ROOM + 1
    robot_map.mapData.mapData = bytes(pixels)
    robot_map.mapInfo.add(mapHeadId=1, mapName="Floor 1")
    room = robot_map.roomDataInfo.add(roomId=ROOM + 1, roomName="Kitchen")
    room.roomNamePost.x, room.roomNamePost.y = island_pixel_to_map(70, 110)
    robot_map.chargeStation.x, robot_map.chargeStation.y = island_pixel_to_map(92, 110)
    return robot_map.SerializeToString()


def map_key(
    model: str, wifi_sn: str, owner_id: str, device_id: str, mac: str = MAC, *, hex_key: bool | None = None
) -> bytes:
    """The AES key; `hex_key` defaults to the model's key type in vacuum-map-parser-ijai 0.1.1."""
    model_key = model.split(".")[-1][-4:].rjust(4, "0")
    aes_key = (mac.replace(":", "").lower() + model_key).encode()
    plain = pad("+".join([wifi_sn, owner_id, device_id]).encode(), AES.block_size, "pkcs7")
    digest = MD5.new(base64.b64encode(AES.new(aes_key, AES.MODE_ECB).encrypt(plain))).hexdigest()
    if hex_key is None:
        hex_key = model.split(".")[-1] in HEX_KEY_MODELS
    if hex_key:
        return bytes.fromhex(digest)
    return digest[8:-8].upper().encode()


def encrypt_map(
    model: str,
    *,
    device_id: str = DEVICE_ID,
    owner_id: str = OWNER_ID,
    wifi_sn: str = WIFI_SN,
    robot_map: bytes | None = None,
    hex_key: bool | None = None,
) -> bytes:
    plain = zlib.compress(build_map() if robot_map is None else robot_map).hex().encode()
    key = map_key(model, wifi_sn, owner_id, device_id, hex_key=hex_key)
    return AES.new(key, AES.MODE_ECB).encrypt(pad(plain, AES.block_size, "pkcs7"))
