"""Every sensor reads a map that has everything a parser can fill in.

The sensors are disabled by default, so a mistake in one only shows when a user enables it and the
vacuum reports that piece of the map.
"""

from __future__ import annotations

import json

import pytest
from vacuum_map_parser_base.map_data import (
    Area,
    MapData,
    Obstacle,
    ObstacleDetails,
    Path,
    Point,
    Room,
    Wall,
    Zone,
)

from custom_components.better_xiaomi_cloud_map_extractor.sensor import SENSOR_TYPES


def _path() -> Path:
    return Path(None, None, None, [[Point(1, 2), Point(3, 4)], [Point(5, 6)]])


def _full_map() -> MapData:
    map_data = MapData()
    area = Area(0, 0, 1, 0, 1, 1, 0, 1)
    obstacle = Obstacle(1, 2, ObstacleDetails())
    map_data.no_go_areas = [area]
    map_data.no_carpet_areas = [area]
    map_data.no_mopping_areas = [area]
    map_data.charger = Point(1, 2, 90)
    map_data.vacuum_position = Point(3, 4, 180)
    map_data.vacuum_room = 16
    map_data.vacuum_room_name = "Kitchen"
    map_data.cleaned_rooms = {16, 17}
    map_data.goto = Point(5, 6)
    map_data.goto_path = _path()
    map_data.predicted_path = _path()
    map_data.mop_path = _path()
    map_data.path = _path()
    map_data.obstacles = [obstacle]
    map_data.ignored_obstacles = [obstacle]
    map_data.obstacles_with_photo = [obstacle]
    map_data.ignored_obstacles_with_photo = [obstacle]
    map_data.walls = [Wall(0, 0, 1, 1)]
    map_data.zones = [Zone(0, 0, 1, 1)]
    map_data.map_name = "map_0"
    map_data.rooms = {16: Room(0, 0, 1, 1, 16, "Kitchen")}
    return map_data


@pytest.mark.parametrize("description", SENSOR_TYPES, ids=[d.key for d in SENSOR_TYPES])
def test_sensor_reads_a_full_map(description) -> None:
    map_data = _full_map()

    value = description.value_fn(map_data)
    attributes = description.attributes_fn(map_data)

    assert value is not None
    json.dumps(attributes, default=str)


@pytest.mark.parametrize("key", ["goto_path", "goto_predicted_path", "mop_path", "path"])
def test_path_sensors_count_the_points(key: str) -> None:
    description = next(d for d in SENSOR_TYPES if d.key == key)

    assert description.value_fn(_full_map()) == 3


@pytest.mark.parametrize("description", SENSOR_TYPES, ids=[d.key for d in SENSOR_TYPES])
def test_sensor_reads_an_empty_map(description) -> None:
    description.value_fn(MapData())
    description.attributes_fn(MapData())
