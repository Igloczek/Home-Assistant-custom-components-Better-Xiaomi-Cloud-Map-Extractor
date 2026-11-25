"""What the MIoT properties of Xiaomi-format vacuums say about the map, and fixes to the map payload.

Which property holds the status, the current map and the live position differs between model
families. The layouts below come from each model's spec on miot-spec.org (recorded in
`tests/fixtures/xiaomi/miot_spec.json`, which the tests check them against):

- `map-obj-name` names the current map's cloud object (`"<uid>/<did>/<map>"`, plain or in JSON);
  `current-map-id` (`cur-map-id` on older models) is its number.
- `trajectory-obj-name` names a second cloud object holding the path of the current clean, and
  `vacuum-position` is `{"position": [x, y, yaw in mrad]}` (upstream PR #781). The cloud map object
  itself has neither (upstream #720).
"""

from __future__ import annotations

import base64
import json
import logging
import struct
import zlib
from dataclasses import dataclass
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from vacuum_map_parser_xiaomi.status_mapping import get_status_mapping

_LOGGER = logging.getLogger(__name__)

Property = tuple[int, int]


@dataclass(frozen=True)
class XiaomiMiotLayout:
    status: Property
    # Statuses during which the robot stands still, so the map does not change.
    idle_at: tuple[int, ...]
    # Statuses at the dock, where the trajectory property still names the previous clean's path.
    docked_at: tuple[int, ...] = ()
    map_obj_name: Property | None = None
    current_map_id: Property | None = None
    trajectory_obj_name: Property | None = None
    vacuum_position: Property | None = None


# b106, b112, c101-c104, e101gl: `cur-map-id` (10/2) is a number; no live position.
_LEGACY = XiaomiMiotLayout(status=(2, 1), idle_at=(0, 1, 2, 4, 8, 10), current_map_id=(10, 2))
# S20+: map service 7; 1 Idle, 2 Charging, 3 BreakCharging, 5 Paused, 8 Charged, 10 Updating.
_B108GL = XiaomiMiotLayout(
    status=(2, 1), idle_at=(1, 2, 3, 5, 8, 10), docked_at=(2, 8),
    map_obj_name=(7, 1), trajectory_obj_name=(7, 2), vacuum_position=(7, 4),
)
# Map service 9; the status is only 1 Idle, 2 Busy, 3 Delay.
_SERVICE_9 = XiaomiMiotLayout(
    status=(2, 2), idle_at=(1, 3),
    map_obj_name=(9, 1), current_map_id=(9, 6), trajectory_obj_name=(9, 2), vacuum_position=(9, 4),
)
# Map service 10, 2/1 is the firmware version; 1 Idle, 2 Charging, 3 BreakCharging, 5 Paused,
# 9 Charged, 11 Updating.
_SERVICE_10 = XiaomiMiotLayout(
    status=(2, 2), idle_at=(1, 2, 3, 5, 9, 11), docked_at=(2, 9),
    map_obj_name=(10, 1), current_map_id=(10, 6), trajectory_obj_name=(10, 2), vacuum_position=(10, 4),
)
# Models whose spec has no map property; their own status values.
_B108 = XiaomiMiotLayout(status=(2, 1), idle_at=(1,))
_C108 = XiaomiMiotLayout(status=(2, 1), idle_at=(1, 3, 5, 7), docked_at=(5, 7))
_C102CN = XiaomiMiotLayout(status=(2, 1), idle_at=(2, 3, 6, 13, 14), docked_at=(6, 13))

_LAYOUTS: dict[XiaomiMiotLayout, tuple[str, ...]] = {
    _LEGACY: ("b106bk", "b106eu", "b106tr", "b112", "b112bk", "b112gl", "b112tr", "c101", "c101eu", "c102",
              "c103", "c104", "d106gl", "e101gl"),
    _B108GL: ("b108gl",),
    _SERVICE_9: ("b108gp", "ov32gl", "ov43gl", "ov51", "ov81"),
    _SERVICE_10: ("c107", "d101", "d102cn", "d102ev", "d102gl", "d105", "d109gl", "d110cn", "e101gb", "ov21cn",
                  "ov21gl", "ov31cn", "ov31gl", "ov42cn", "ov42gl", "ov43gb", "ov51gl", "ov71gl", "ov81cn",
                  "ov81gl", "pv11cn", "pv11gl", "pv21cn", "pv21gl", "pv31cn", "pv32cn", "pv32gl", "pv51gl",
                  "pv81gl", "qv12cn", "qv12gl", "qv41cn", "qv41gl", "qv42gl", "qv71gl"),
    _B108: ("b108",),
    _C108: ("c108",),
    _C102CN: ("c102cn", "c102gl", "d103cn", "d110ch"),
}
_BY_MODEL = {f"xiaomi.vacuum.{suffix}": layout for layout, suffixes in _LAYOUTS.items() for suffix in suffixes}


def layout_for(model: str) -> XiaomiMiotLayout:
    if model in _BY_MODEL:
        return _BY_MODEL[model]
    # A model the spec list does not know reads what v3.0.0-alpha-24 read.
    status = get_status_mapping(model)
    return XiaomiMiotLayout(status=(status.siid, status.piid), idle_at=tuple(status.idle_at), map_obj_name=(10, 1))


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def object_name(value: Any) -> str | None:
    """The last part of an object name property: a number, `{"obj_name": "a/b/c"}` or `"a/b/c"`."""
    if as_int(value) is not None:
        return str(value)
    path = object_path(value)
    if path is not None:
        return path.split("/")[-1]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return str(parsed) if as_int(parsed) is not None else None
    return None


def object_path(value: Any) -> str | None:
    """The whole object name (`"<uid>/<did>/<name>"`) when the property carries a path.

    The uid is the one the vacuum stores its objects under, which may not be the account's
    (upstream #762).
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    if isinstance(parsed, dict):
        parsed = parsed.get("obj_name")
    if not isinstance(parsed, str) or "/" not in parsed or not parsed.split("/")[-1]:
        return None
    return parsed


# The parser's marker for a position the robot does not know.
POSITION_UNKNOWN = 1100


def vacuum_position(value: Any) -> dict[str, Any] | None:
    """`{"position": [x, y, yaw in mrad]}` as the parser's position (yaw in radians)."""
    try:
        position = json.loads(value).get("position") if isinstance(value, str) else None
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(position, list) or len(position) < 2:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in position[:3]):
        return None
    if POSITION_UNKNOWN in position[:2] or not any(position[:2]):
        return None
    yaw = position[2] / 1000 if len(position) > 2 else 0
    return {"x": position[0], "y": position[1], "yaw": yaw}


_TRAJECTORY_RECORD = struct.Struct("<Bii")
_TRAJECTORY_MARKERS = (2, 3)  # 2 sweeping, 3 mopping
_IV = b"ABCDEF1234123412"


def decode_trajectory(raw: bytes, key: bytes | None = None) -> list[dict[str, Any]]:
    """The points of a trajectory object: zlib of `<u8 marker, i32 x, i32 y>` records (PR #781).

    The object may come wrapped like the map (`{"data": base64}`) and, if it does not inflate,
    encrypted with the map key.
    """
    try:
        raw = base64.b64decode(json.loads(raw)["data"])
    except (ValueError, KeyError, TypeError):
        try:
            raw = base64.b64decode(raw, validate=True)
        except ValueError:
            pass
    data = _inflate(raw)
    if data is None and key is not None and raw and len(raw) % AES.block_size == 0:
        try:
            data = _inflate(unpad(AES.new(key, AES.MODE_CBC, _IV).decrypt(raw), AES.block_size))
        except ValueError:
            data = None
    if data is None:
        data = raw
    points, i = [], 0
    while i + _TRAJECTORY_RECORD.size <= len(data):
        marker, x, y = _TRAJECTORY_RECORD.unpack_from(data, i)
        if marker not in _TRAJECTORY_MARKERS or abs(x) > 1_000_000 or abs(y) > 1_000_000:
            i += 1  # header or out-of-step byte
            continue
        points.append({"x": x, "y": y})
        i += _TRAJECTORY_RECORD.size
    return points


def _inflate(data: bytes) -> bytes | None:
    try:
        return zlib.decompress(data)
    except zlib.error:
        return None


def with_live_data(
    payload: dict[str, Any], path: list[dict[str, Any]], position: dict[str, Any] | None
) -> dict[str, Any]:
    """The map payload with the live path and position the cloud map lacks (PR #781)."""
    if not path and position is None:
        return payload
    payload = dict(payload)
    if path and not payload.get("paths"):
        payload["paths"] = path
    if position is not None:
        payload["position"] = position
    elif path and not isinstance(payload.get("position"), dict):
        payload["position"] = {**path[-1], "yaw": 0}
    return payload


def with_unique_room_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Rooms keyed by grid id when `map_room_info` repeats a `room_id` (PR #748, generalised).

    The ov21gl gives every room `room_id` 0, and the parser keys rooms by `room_id`, so all rooms
    collapsed into one. `room_attrs` then name rooms by grid id (`id`), so their repeated
    `room_id` is dropped too.
    """
    info = payload.get("map_room_info")
    if not isinstance(info, list):
        return payload
    entries = [e for e in info if isinstance(e, dict) and e.get("grid_id") is not None]
    room_ids = [e.get("room_id") for e in entries]
    if len(set(room_ids)) == len(room_ids):
        return payload
    _LOGGER.debug("map_room_info repeats room ids, keying rooms by grid id")
    payload = dict(payload)
    payload["map_room_info"] = [
        {**e, "room_id": e["grid_id"]} if isinstance(e, dict) and e.get("grid_id") is not None else e for e in info
    ]
    attrs = payload.get("room_attrs")
    if isinstance(attrs, list):
        payload["room_attrs"] = [
            {k: v for k, v in a.items() if k != "room_id"}
            if isinstance(a, dict) and (a.get("grid_id") is not None or a.get("id") is not None) else a
            for a in attrs
        ]
    return payload
