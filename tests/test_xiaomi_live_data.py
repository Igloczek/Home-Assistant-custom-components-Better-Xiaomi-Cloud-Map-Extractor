"""Xiaomi-format vacuums: what the MIoT properties add to the cloud map.

The cloud map object of the newer Xiaomi vacuums carries the floor plan, rooms and forbidden areas,
but no robot position or path (upstream #720: a decoded b108gl map had only `map_data`,
`map_room_info`, `room_attrs` and the dock). The vacuum publishes those as MIoT properties:
`vacuum-position` (`{"position": [x, y, yaw in mrad]}`) and `trajectory-obj-name`, the name of a
second cloud object with the path (upstream PR #781). Which service holds them, and where the
status is, differs between model families; `tests/fixtures/xiaomi/miot_spec.json` records them from
miot-spec.org.

The vacuum on the LAN is `FakeLanVacuum` under the real python-miio; the cloud is a mocked
connector that serves objects by name. Every map, trajectory and id here is synthetic.
"""

from __future__ import annotations

import base64
import json
import logging
import struct
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from miio.exceptions import DeviceException
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_xiaomi import (
    OFF_UPDATES,
    XiaomiCloudVacuum,
)

from .conftest import make_device
from .fake_miio import FakeLanVacuum, lan_vacuum
from .log_secrets import assert_no_secrets
from .test_vacuum_xiaomi import DEVICE_ID, USER_ID, app_key, encrypt_xiaomi_map

FIXTURES = Path(__file__).parent / "fixtures" / "xiaomi"
MIOT_SPEC = json.loads((FIXTURES / "miot_spec.json").read_text())["models"]
OBJECT_URL = "https://example.invalid/objects/"


TOKEN = "0123456789abcdef0123456789abcdef"
SCANNED_MODELS = ("xiaomi.vacuum.b108gl", "xiaomi.vacuum.ov81gl", "xiaomi.vacuum.ov32gl", "xiaomi.vacuum.ov21gl",
                  "xiaomi.vacuum.ov43gb", "xiaomi.vacuum.e101gb", "xiaomi.vacuum.c101")


@pytest.fixture(autouse=True)
def no_secrets_logged(caplog: pytest.LogCaptureFixture):
    """Every scenario runs at DEBUG and ends with the secrets scan: no device token or map key logged."""
    caplog.set_level(logging.DEBUG)
    yield
    if caplog.records:
        assert_no_secrets(caplog, {
            "device token": TOKEN, **{f"map key {m}": app_key(m, DEVICE_ID).hex() for m in SCANNED_MODELS},
        })


def fixture_map(name: str) -> str:
    return (FIXTURES / name).read_text()


def obj_name(name: str, uid: str = str(USER_ID)) -> str:
    return json.dumps({"index": 1770000000, "obj_name": f"{uid}/{DEVICE_ID}/{name}"})


def encode_trajectory(points: list[tuple[int, int]], marker: int = 2) -> bytes:
    """A trajectory object as PR #781 reads it: zlib of a header and `<u8 marker, i32 x, i32 y>` records."""
    header = struct.pack("<HH", 1, len(points))
    return zlib.compress(header + b"".join(struct.pack("<Bii", marker, x, y) for x, y in points))


class Cloud:
    """The Xiaomi cloud: the vacuum's objects, and `/v2/home/rpc` forwarded to the vacuum (`cloud_rpc`).

    The objects live under `<owner_uid>/<did>/<name>`. Like the real cloud (upstream #762), it signs a
    URL for a path under any uid, and the download of a path where no object is answers 404 (None).
    """

    def __init__(self, objects: dict[str, bytes], vacuum: FakeLanVacuum | None = None,
                 owner_uid: str = str(USER_ID)) -> None:
        self.objects = objects
        self.vacuum = vacuum
        self.owner_uid = owner_uid
        self.connector = MagicMock()
        self.connector.get_api_url = MagicMock(return_value="https://de.api.io.mi.com/app")
        self.connector.execute_api_call_encrypted = AsyncMock(side_effect=self._file_url)
        self.connector.get_raw_map_data = AsyncMock(side_effect=self._download)
        self.connector.get_other_info = AsyncMock(side_effect=self._rpc)

    async def _file_url(self, _url: str, params: dict[str, str]) -> dict[str, Any]:
        path = json.loads(params["data"])["obj_name"]
        if path.split("/")[-1] not in self.objects:
            return {"code": 0, "result": {}}
        return {"code": 0, "result": {"url": OBJECT_URL + path}}

    async def _download(self, url: str | None) -> bytes | None:
        if url is None:
            return None
        uid, did, name = url.removeprefix(OBJECT_URL).split("/")
        if (uid, did) != (self.owner_uid, DEVICE_ID):
            return None  # "404 Object Not Found"
        return self.objects.get(name)

    async def _rpc(self, did: str, method: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        assert did == DEVICE_ID
        if self.vacuum is None:
            return {"code": -2, "message": "device offline"}
        return self.vacuum.cloud_rpc({"method": method, "params": parameters})

    def requested(self) -> list[str]:
        """The object paths whose download was tried."""
        return [c.args[0].removeprefix(OBJECT_URL) for c in self.connector.get_raw_map_data.await_args_list if c.args[0]]

    def downloaded(self) -> list[str]:
        return [path.split("/")[-1] for path in self.requested()]


def make_vacuum(model: str, cloud: Cloud) -> XiaomiCloudVacuum:
    config = VacuumConfig(
        connector=cloud.connector,
        device_info=make_device(model=model, device_id=DEVICE_ID, user_id=USER_ID),
        server="de",
        device_id=DEVICE_ID,
        host="192.168.1.50",
        token=TOKEN,
        model=model,
        palette=ColorsPalette(),
        drawables=[],
        image_config=ImageConfig(),
        sizes=Sizes(),
        texts=[],
    )
    return XiaomiCloudVacuum(config)


# The three property layouts of the Xiaomi models with a trajectory (miot_spec.json).
CLEANING = {
    # S20+: map service 7, status 2/1 where 4 is "Sweeping" (upstream PR #781).
    "xiaomi.vacuum.b108gl": {(2, 1): 4, (7, 1): obj_name("3"), (7, 2): obj_name("traj-17"),
                             (7, 4): json.dumps({"position": [120, -80, 1571]})},
    # S40 / X20+ family: map service 10, 2/1 is the firmware version, the status is 2/2.
    "xiaomi.vacuum.ov81gl": {(2, 1): "4.3.3_0016", (2, 2): 4, (10, 1): obj_name("3"),
                             (10, 2): obj_name("traj-17"), (10, 4): json.dumps({"position": [120, -80, 1571]})},
    # Map service 9, status 2/2 is only Idle / Busy / Delay.
    "xiaomi.vacuum.ov32gl": {(2, 1): "1.2.0_0001", (2, 2): 2, (9, 1): obj_name("3"),
                             (9, 2): obj_name("traj-17"), (9, 4): json.dumps({"position": [120, -80, 1571]})},
}
DOCKED_STATUS = {"xiaomi.vacuum.b108gl": ((2, 1), 2), "xiaomi.vacuum.ov81gl": ((2, 2), 9)}
PATH = [(-100, -50), (0, 0), (120, -80)]


def cleaning_cloud(model: str, map_file: str = "map_without_live_data.json", **objects: bytes) -> Cloud:
    return Cloud({
        "3": encrypt_xiaomi_map(fixture_map(map_file), model, DEVICE_ID),
        "traj-17": encode_trajectory(PATH),
        **objects,
    })


# --- live position and path ----------------------------------------------------------------------


@pytest.mark.parametrize("model", CLEANING)
async def test_cleaning_vacuum_shows_its_position_and_path(model: str) -> None:
    """Upstream #720 / PR #781: robot and path were missing while cleaning."""
    cloud = cleaning_cloud(model)
    lan = FakeLanVacuum(model, properties=dict(CLEANING[model]))
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, cloud)
        map_data, _ = await vacuum.get_map()

    assert map_data.map_name == "3"
    assert [(p.x, p.y) for p in map_data.path.path[0]] == PATH
    position = map_data.vacuum_position
    assert (position.x, position.y) == (120, -80)
    assert round(position.a) == 90  # 1571 mrad


@pytest.mark.parametrize("model", DOCKED_STATUS)
async def test_docked_vacuum_does_not_show_the_last_trajectory(model: str) -> None:
    """Docked, the trajectory property still names the previous clean's path (PR #781)."""
    status, docked = DOCKED_STATUS[model]
    cloud = cleaning_cloud(model)
    lan = FakeLanVacuum(model, properties={**CLEANING[model], status: docked})
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, cloud)
        map_data, _ = await vacuum.get_map()

    assert cloud.downloaded() == ["3"]
    assert map_data.path is None


async def test_path_in_the_cloud_map_is_kept() -> None:
    """A map that has its own path keeps it; the trajectory object only fills a gap."""
    model = "xiaomi.vacuum.ov81gl"
    payload = json.loads(fixture_map("map.json"))
    payload["paths"] = [{"x": 10, "y": 20}, {"x": 30, "y": 40}]
    cloud = Cloud({"3": encrypt_xiaomi_map(json.dumps(payload), model, DEVICE_ID),
                   "traj-17": encode_trajectory(PATH)})
    with lan_vacuum(FakeLanVacuum(model, properties=dict(CLEANING[model]))):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert [(p.x, p.y) for p in map_data.path.path[0]] == [(10, 20), (30, 40)]


@pytest.mark.parametrize(
    "position",
    [json.dumps({"position": [1100, 1100, 0]}), json.dumps({"position": [0, 0, 0]}), "not json", None],
    ids=["unknown-marker", "origin", "garbage", "missing"],
)
async def test_unknown_position_keeps_the_map_position(position: str | None) -> None:
    """1100 is the parser's "position unknown" marker; nothing usable leaves the map's own position."""
    model = "xiaomi.vacuum.ov81gl"
    properties = {**CLEANING[model], (10, 4): position}
    if position is None:
        del properties[(10, 4)]
    cloud = Cloud({"3": encrypt_xiaomi_map(fixture_map("map.json"), model, DEVICE_ID)})
    with lan_vacuum(FakeLanVacuum(model, properties=properties)):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert (map_data.vacuum_position.x, map_data.vacuum_position.y) == (50, 50)  # map.json


async def test_unreadable_trajectory_never_fails_the_map() -> None:
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model, **{"traj-17": b"\x00garbage that is neither zlib nor a trajectory"})
    with lan_vacuum(FakeLanVacuum(model, properties=dict(CLEANING[model]))):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert map_data.image.dimensions.width == 10
    assert map_data.path is None


async def test_missing_trajectory_object_never_fails_the_map() -> None:
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    del cloud.objects["traj-17"]
    with lan_vacuum(FakeLanVacuum(model, properties=dict(CLEANING[model]))):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert map_data.path is None
    assert (map_data.vacuum_position.x, map_data.vacuum_position.y) == (120, -80)


# --- the status that says whether the map moves --------------------------------------------------


@pytest.mark.parametrize(
    ("model", "cleaning", "docked"),
    [
        ("xiaomi.vacuum.b108gl", {(2, 1): 4}, {(2, 1): 2}),  # 4 is "Sweeping", was treated as idle (PR #781)
        ("xiaomi.vacuum.ov81gl", {(2, 1): "4.3.3_0016", (2, 2): 4}, {(2, 1): "4.3.3_0016", (2, 2): 9}),
        ("xiaomi.vacuum.ov43gb", {(2, 1): "4.3.3_0016", (2, 2): 16}, {(2, 1): "4.3.3_0016", (2, 2): 2}),
        ("xiaomi.vacuum.e101gb", {(2, 1): "4.1.0_0010", (2, 2): 17}, {(2, 1): "4.1.0_0010", (2, 2): 9}),
        ("xiaomi.vacuum.ov32gl", {(2, 1): "1.2.0_0001", (2, 2): 2}, {(2, 1): "1.2.0_0001", (2, 2): 1}),
        ("xiaomi.vacuum.c101", {(2, 1): 5}, {(2, 1): 4}),
    ],
)
async def test_status_comes_from_the_models_status_property(
    model: str, cleaning: dict, docked: dict
) -> None:
    """The status is 2/2 on most models, where 2/1 is the firmware version (upstream #764)."""
    lan = FakeLanVacuum(model, properties=dict(cleaning))
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, Cloud({}))
        assert [await vacuum.should_update_map() for _ in range(OFF_UPDATES + 2)] == [True] * (OFF_UPDATES + 2)

        lan.properties = dict(docked)
        moving = [await vacuum.should_update_map() for _ in range(OFF_UPDATES + 1)]
    assert moving == [True] * OFF_UPDATES + [False]


async def test_vacuum_unreachable_on_the_lan_cannot_tell_whether_it_moves() -> None:
    """PR #748: a vacuum too busy to answer on the LAN stopped every map download (False)."""
    model = "xiaomi.vacuum.ov21gl"
    lan = FakeLanVacuum(model, reachable=False)
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, Cloud({}))
        assert await vacuum.should_update_map() is None


# --- which map ------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CLEANING)
async def test_current_map_through_the_cloud_when_the_lan_does_not_answer(model: str) -> None:
    """Without the LAN the current map was unknown and the update failed ("vacuum unreachable")."""
    lan = FakeLanVacuum(model, properties=dict(CLEANING[model]), reachable=False)
    cloud = cleaning_cloud(model)
    cloud.vacuum = lan
    with lan_vacuum(lan):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert [call["method"] for call in lan.cloud_calls] == ["get_properties"]

    assert map_data.map_name == "3"
    assert [(p.x, p.y) for p in map_data.path.path[0]] == PATH


async def test_current_map_id_when_the_map_object_name_is_missing() -> None:
    """Upstream #720: map "0" is a deleted map on a vacuum that had several; current-map-id knows."""
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    properties = {(2, 2): 1, (10, 6): 3}
    with lan_vacuum(FakeLanVacuum(model, properties=properties)):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert map_data.map_name == "3"


async def test_vacuum_unreachable_everywhere_is_reported_not_guessed() -> None:
    """No answer from the LAN or the cloud: the update fails as unreachable instead of showing map "0"."""
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    cloud.objects["0"] = cloud.objects["3"]
    cloud.vacuum = FakeLanVacuum(model, properties=dict(CLEANING[model]), reachable=False, online=False)
    with lan_vacuum(cloud.vacuum), pytest.raises(DeviceException):
        await make_vacuum(model, cloud).get_map()

    assert cloud.downloaded() == []


async def test_last_reported_map_is_kept_when_the_vacuum_stops_answering() -> None:
    """The LAN drops and the cloud has no properties: the map the vacuum last named, not map "0"."""
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    cloud.objects["0"] = cloud.objects["3"]
    lan = FakeLanVacuum(model, properties={(2, 2): 2, (10, 1): obj_name("3")})
    cloud.vacuum = lan
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, cloud)
        await vacuum.get_map()
        assert vacuum.lan_reachable is True

        lan.reachable = False
        lan.online = False
        map_data, _ = await vacuum.get_map()

    assert map_data.map_name == "3"
    assert cloud.downloaded() == ["3", "3"]
    assert vacuum.lan_reachable is False


@pytest.mark.parametrize("cloud_answers", [True, False], ids=["cloud-fallback", "nothing"])
async def test_lan_reachable_says_whether_the_vacuum_answered_on_the_lan(cloud_answers: bool) -> None:
    """The vacuum_unreachable repair reads `lan_reachable`: False even when the cloud supplied the map."""
    model = "xiaomi.vacuum.ov81gl"
    lan = FakeLanVacuum(model, properties=dict(CLEANING[model]), online=cloud_answers)
    cloud = cleaning_cloud(model)
    cloud.vacuum = lan
    with lan_vacuum(lan):
        vacuum = make_vacuum(model, cloud)
        assert await vacuum.should_update_map() is True
        assert vacuum.lan_reachable is True

        lan.reachable = False
        assert await vacuum.should_update_map() is None
        assert vacuum.lan_reachable is False
        lan.reachable = True
        await vacuum.get_map()
        assert vacuum.lan_reachable is True

        lan.reachable = False
        map_data, _ = await vacuum.get_map()  # from the cloud, or the map of the previous answer
        assert map_data.map_name == "3"
        assert vacuum.lan_reachable is False


# Upstream #762: on an H40 the vacuum's objects sit under a uid other than the account's (scrubbed: the
# report's uids are replaced by made-up ones of the same length).
VACUUM_UID = "5550001111"


async def test_map_and_trajectory_come_from_the_path_the_vacuum_reports() -> None:
    """The path was rebuilt with the account's uid, signed fine, and downloaded "404 Object Not Found"."""
    model = "xiaomi.vacuum.ov51gl"
    cloud = cleaning_cloud(model)
    cloud.owner_uid = VACUUM_UID
    lan = FakeLanVacuum(model, properties={
        (2, 1): "4.5.6_0105", (2, 2): 4, (10, 1): obj_name("0", VACUUM_UID),
        (10, 2): obj_name("traj-17", VACUUM_UID), (10, 4): json.dumps({"position": [120, -80, 1571]}),
    })
    cloud.objects["0"] = cloud.objects.pop("3")
    with lan_vacuum(lan):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert cloud.requested() == [f"{VACUUM_UID}/{DEVICE_ID}/traj-17", f"{VACUUM_UID}/{DEVICE_ID}/0"]
    assert map_data.map_name == "0"
    assert [(p.x, p.y) for p in map_data.path.path[0]] == PATH


async def test_account_path_is_tried_when_the_reported_path_has_no_object() -> None:
    """Never worse than before: if nothing is under the reported uid, the account's path is tried."""
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    lan = FakeLanVacuum(model, properties={(2, 2): 1, (10, 1): obj_name("3", VACUUM_UID)})
    with lan_vacuum(lan):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert cloud.requested() == [f"{VACUUM_UID}/{DEVICE_ID}/3", f"{USER_ID}/{DEVICE_ID}/3"]
    assert map_data.map_name == "3"


async def test_map_name_without_a_path_uses_the_account_path() -> None:
    model = "xiaomi.vacuum.ov81gl"
    cloud = cleaning_cloud(model)
    with lan_vacuum(FakeLanVacuum(model, properties={(2, 2): 1, (10, 1): None, (10, 6): 3})):
        await make_vacuum(model, cloud).get_map()

    assert cloud.requested() == [f"{USER_ID}/{DEVICE_ID}/3"]


async def test_legacy_models_read_the_numeric_map_id() -> None:
    """b106/b112/c10x/e101gl report `cur-map-id` (10/2) as a number."""
    model = "xiaomi.vacuum.c101"
    cloud = Cloud({"5": encrypt_xiaomi_map(fixture_map("map.json"), model, DEVICE_ID)})
    with lan_vacuum(FakeLanVacuum(model, properties={(2, 1): 1, (10, 2): 5})):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert map_data.map_name == "5"


# --- the property layouts against the MIoT spec ---------------------------------------------------

# Status descriptions during which the robot stands still, so the map cannot change.
STANDING = {"Sleep", "Sleeping", "Idle", "Paused", "Charging", "BreakCharging", "Charged", "Charge Done",
            "Charging Completed", "Upgrade", "Upgrading", "Updating", "Delay", "In The Dry"}
DOCKED = {"Charging", "Charged", "Charge Done", "Charging Completed"}


@pytest.mark.parametrize("model", sorted(MIOT_SPEC))
def test_property_layout_matches_the_miot_spec(model: str) -> None:
    from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.xiaomi_miot import layout_for

    spec = MIOT_SPEC[model]
    layout = layout_for(model)

    assert layout.status == tuple(spec["status"])
    assert layout.map_obj_name == tuple_or_none(spec.get("map_obj_name"))
    assert layout.current_map_id == tuple_or_none(spec.get("current_map_id"))
    assert layout.trajectory_obj_name == tuple_or_none(spec.get("trajectory_obj_name"))
    assert layout.vacuum_position == tuple_or_none(spec.get("vacuum_position"))
    values = spec["status_values"]
    # A family shares one list; a value this model does not have never arrives.
    assert {values[str(v)] for v in layout.idle_at if str(v) in values} <= STANDING
    assert {values[str(v)] for v in layout.docked_at} <= DOCKED
    assert "Idle" in {values[str(v)] for v in layout.idle_at if str(v) in values}


def test_unknown_model_keeps_the_previous_properties() -> None:
    """A model the spec fixture does not list reads what v3.0.0-alpha-24 read (map 10/1, status 2/1)."""
    from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.xiaomi_miot import layout_for

    layout = layout_for("xiaomi.vacuum.zz99gl")

    assert layout.status == (2, 1)
    assert layout.map_obj_name == (10, 1)
    assert layout.current_map_id is None


def tuple_or_none(value: list[int] | None) -> tuple[int, int] | None:
    return tuple(value) if value else None


# --- what the cloud map itself holds --------------------------------------------------------------


async def parse_fixture(model: str, payload: str) -> Any:
    cloud = Cloud({"3": encrypt_xiaomi_map(payload, model, DEVICE_ID)})
    with lan_vacuum(FakeLanVacuum(model, properties={(2, 2): 1, (10, 1): obj_name("3")})):
        map_data, _ = await make_vacuum(model, cloud).get_map()
    return map_data


async def test_rooms_with_room_id_zero_stay_separate() -> None:
    """Upstream #702 / PR #748: the ov21gl gives every room `room_id` 0, and all rooms became one."""
    map_data = await parse_fixture("xiaomi.vacuum.ov21gl", fixture_map("map_room_id_zero.json"))

    assert sorted(room.name for room in map_data.rooms.values()) == ["Hall", "Kitchen"]
    assert sorted(map_data.rooms) == [3, 4]  # the grid ids, which room_attrs use


async def test_rooms_with_a_repeated_room_id_stay_separate() -> None:
    """The same collapse for any repeated `room_id`, not only 0 on every room."""
    payload = json.loads(fixture_map("map.json"))
    for entry in payload["map_room_info"]:
        entry["room_id"] = 7
    payload["room_attrs"] = [{"id": 3, "room_id": 7, "name": "Kitchen"}, {"id": 4, "room_id": 7, "name": "Hall"}]
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", json.dumps(payload))

    assert sorted(room.name for room in map_data.rooms.values()) == ["Hall", "Kitchen"]


async def test_unique_room_ids_are_kept() -> None:
    """Room ids that are unique are the ids users clean rooms by; they stay as they are."""
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", fixture_map("map.json"))

    assert {room_id: room.name for room_id, room in map_data.rooms.items()} == {1: "Kitchen", 2: "Hall"}


# Room keys: the `rooms` attribute's keys are the room ids the map card sends back to the vacuum, so they
# must not depend on what one payload happens to contain. A map of four rooms, one per quadrant:
#   grid 3 | grid 4
#   grid 5 | grid 6
FOUR_ROOMS_PIXELS = bytes(
    [255] * 10
    + ([255, 3, 3, 3, 3, 4, 4, 4, 4, 255] * 3)
    + ([255, 5, 5, 5, 5, 6, 6, 6, 6, 255] * 3)
    + [255] * 10
)
TWO_ROOMS_PIXELS = bytes(p if p in (3, 4, 255) else 255 for p in FOUR_ROOMS_PIXELS)
ONE_ROOM_PIXELS = bytes([255] * 10 + [255, 3, 3, 3, 3, 3, 3, 3, 3, 255] * 6 + [255] * 10)


def rooms_map(rooms: list[tuple[int, int]], attrs: list[dict[str, Any]], pixels: bytes = FOUR_ROOMS_PIXELS) -> str:
    """A synthetic map whose `map_room_info` pairs each `(grid_id, room_id)` and whose `room_attrs` are `attrs`."""
    payload = json.loads(fixture_map("map_without_live_data.json"))
    payload["map_data"] = base64.b64encode(zlib.compress(pixels)).decode()
    payload["map_room_info"] = [{"grid_id": g, "room_id": r, "color": i} for i, (g, r) in enumerate(rooms, 1)]
    payload["room_attrs"] = attrs
    return json.dumps(payload)


def bounds(room: Any) -> tuple[float, float, float, float]:
    return room.x0, room.y0, room.x1, room.y1


async def grid_bounds(pixels: bytes = FOUR_ROOMS_PIXELS) -> dict[int, tuple[float, float, float, float]]:
    """Where each grid id's room lies: the same map with every room keyed by its own grid id."""
    grids = sorted(set(pixels) - {255})
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", rooms_map([(g, g) for g in grids], [], pixels))
    assert sorted(map_data.rooms) == grids
    return {room_id: bounds(room) for room_id, room in map_data.rooms.items()}


def names(map_data: Any) -> dict[int, str | None]:
    return {room_id: room.name for room_id, room in map_data.rooms.items()}


async def test_one_room_with_room_id_zero_keeps_key_zero() -> None:
    """One room with `room_id` 0 on a model other than the ov21gl: keyed 0, as in 1.0.0."""
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", rooms_map(
        [(3, 0)], [{"id": 3, "room_id": 0, "name": "Living room"}], ONE_ROOM_PIXELS))

    assert names(map_data) == {0: "Living room"}


async def test_ov21gl_one_room_is_keyed_by_grid_id() -> None:
    """The ov21gl's rooms are always keyed by grid id, also on a one-room map (its room ids are all 0)."""
    map_data = await parse_fixture("xiaomi.vacuum.ov21gl", rooms_map(
        [(3, 0)], [{"id": 3, "room_id": 0, "name": "Living room"}], ONE_ROOM_PIXELS))

    assert names(map_data) == {3: "Living room"}


async def test_ov21gl_four_rooms_are_keyed_by_grid_id_on_their_own_geometry() -> None:
    grid = await grid_bounds()
    map_data = await parse_fixture("xiaomi.vacuum.ov21gl", rooms_map(
        [(3, 0), (4, 0), (5, 0), (6, 0)],
        [{"id": 3, "room_id": 0, "name": "Kitchen"}, {"id": 4, "room_id": 0, "name": "Hall"},
         {"id": 5, "room_id": 0, "name": "Bed"}, {"id": 6, "room_id": 0, "name": "Bath"}]))

    assert names(map_data) == {3: "Kitchen", 4: "Hall", 5: "Bed", 6: "Bath"}
    assert {room_id: bounds(room) for room_id, room in map_data.rooms.items()} == grid


async def test_two_rooms_with_the_same_room_id_are_keyed_by_grid_id() -> None:
    """Every entry repeats one room id (two or more rooms): keyed by grid id, names on their own rooms."""
    grid = await grid_bounds(TWO_ROOMS_PIXELS)
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", rooms_map(
        [(3, 0), (4, 0)], [{"id": 3, "room_id": 0, "name": "Kitchen"}, {"id": 4, "room_id": 0, "name": "Hall"}],
        TWO_ROOMS_PIXELS))

    assert names(map_data) == {3: "Kitchen", 4: "Hall"}
    assert bounds(map_data.rooms[3]) == grid[3]
    assert bounds(map_data.rooms[4]) == grid[4]


async def test_a_partly_repeated_room_id_keeps_the_room_ids_the_vacuum_sends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ids 1, 2, 3 and a fourth grid repeating 3: keys stay 1, 2, 3 as in 1.0.0, names on their rooms.

    1.0.2 re-keyed every room by grid id here: key 3 was named "Bed" on the kitchen's geometry and
    4, 5, 6 had no name, so the card sent the vacuum room ids it does not know.
    """
    grid = await grid_bounds()
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", rooms_map(
        [(3, 1), (4, 2), (5, 3), (6, 3)],
        [{"room_id": 1, "name": "Kitchen"}, {"room_id": 2, "name": "Hall"}, {"room_id": 3, "name": "Bed"}]))

    assert sorted(map_data.rooms) == [1, 2, 3]
    assert map_data.rooms[1].name == "Kitchen"
    assert bounds(map_data.rooms[1]) == grid[3]
    assert map_data.rooms[2].name == "Hall"
    assert bounds(map_data.rooms[2]) == grid[4]
    assert map_data.rooms[3].name == "Bed"
    assert bounds(map_data.rooms[3]) in (grid[5], grid[6])
    assert any("xiaomi.vacuum.ov81gl" in r.getMessage() and "room id" in r.getMessage() for r in caplog.records)


async def test_named_rooms_and_two_rooms_with_room_id_zero_keep_their_room_ids() -> None:
    """Named rooms 1 and 2 plus two rooms with 0: keys 1, 2, 0 as in 1.0.0, not four unnamed grid ids."""
    grid = await grid_bounds()
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", rooms_map(
        [(3, 1), (4, 2), (5, 0), (6, 0)],
        [{"id": 3, "room_id": 1, "name": "Kitchen"}, {"id": 4, "room_id": 2, "name": "Hall"}]))

    assert sorted(map_data.rooms) == [0, 1, 2]
    assert map_data.rooms[1].name == "Kitchen"
    assert bounds(map_data.rooms[1]) == grid[3]
    assert map_data.rooms[2].name == "Hall"
    assert bounds(map_data.rooms[2]) == grid[4]


async def test_partly_repeated_room_ids_are_logged_once_per_vacuum(caplog: pytest.LogCaptureFixture) -> None:
    model = "xiaomi.vacuum.ov81gl"
    cloud = Cloud({"3": encrypt_xiaomi_map(rooms_map([(3, 1), (4, 2), (5, 3), (6, 3)], []), model, DEVICE_ID)})
    with lan_vacuum(FakeLanVacuum(model, properties={(2, 2): 1, (10, 1): obj_name("3")})):
        vacuum = make_vacuum(model, cloud)
        await vacuum.get_map()
        await vacuum.get_map()

    logged = [r for r in caplog.records if model in r.getMessage() and "room id" in r.getMessage()]
    assert len(logged) == 1
    assert logged[0].levelno == logging.DEBUG


async def test_live_path_mostly_outside_the_map_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """The trajectory decoder scans for marker bytes (confirmed on the S20+ only): a record layout it
    misreads gives points far outside the map, which must not be drawn or exposed as `path`."""
    model = "xiaomi.vacuum.ov81gl"
    garbage = [(-100, -50), (90_000, 40_000), (-70_000, 120_000), (500_000, -300_000)]
    cloud = cleaning_cloud(model, **{"traj-17": encode_trajectory(garbage)})
    with lan_vacuum(FakeLanVacuum(model, properties=dict(CLEANING[model]))):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert map_data.path is None
    position = map_data.vacuum_position
    assert (position.x, position.y) == (120, -80)  # the position property is not the path
    assert any("path" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)


async def test_live_path_with_a_few_points_outside_the_map_is_kept() -> None:
    model = "xiaomi.vacuum.ov81gl"
    points = [(-100, -50), (0, 0), (120, -80), (900, 0)]
    cloud = cleaning_cloud(model, **{"traj-17": encode_trajectory(points)})
    with lan_vacuum(FakeLanVacuum(model, properties=dict(CLEANING[model]))):
        map_data, _ = await make_vacuum(model, cloud).get_map()

    assert [(p.x, p.y) for p in map_data.path.path[0]] == points


async def test_no_go_and_no_mop_zones_and_virtual_walls() -> None:
    """Upstream #739 / PR #748: `fb_regions` with `fb_point` and `fb_walls` were not drawn (S40, 5 Pro)."""
    map_data = await parse_fixture("xiaomi.vacuum.ov81gl", fixture_map("map_forbidden_areas.json"))

    assert [a.as_list() for a in map_data.no_go_areas] == [[-200, -150, -100, -150, -100, -50, -200, -50]]
    assert [a.as_list() for a in map_data.no_mopping_areas] == [[100, 50, 200, 50, 200, 150, 100, 150]]
    assert [w.as_list() for w in map_data.walls] == [[0, -150, 0, 150]]
