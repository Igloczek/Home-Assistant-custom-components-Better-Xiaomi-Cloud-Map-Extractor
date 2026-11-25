import json
from typing import Any, Self

from vacuum_map_parser_base.map_data import MapData
from vacuum_map_parser_roidmi.map_data_parser import RoidmiMapDataParser
from .base.model import VacuumConfig, VacuumApi
from .base.cloud_run_state import ROIDMI_RUN_STATES, CloudRunState
from .base.vacuum_v2 import BaseXiaomiCloudVacuumV2

# Models whose map the Mi Home app reads from `get_interim_file_url_pro`, in a layout where the
# bitmap is the `width * height` bytes right before the JSON (upstream #572, PR #768). For them,
# `get_interim_file_url` still answers, with a URL to a file that is no map.
PRO_MAP_MODELS = frozenset({"roidmi.vacuum.sdj60"})


class RoidmiProMapDataParser(RoidmiMapDataParser):
    """The Roidmi parser with the bitmap found from the JSON instead of a trailing outside cell.

    vacuum-map-parser-roidmi takes the bitmap from byte 16 up to the first outside cell (127)
    followed by `{`. In the pro maps the header is not always 16 bytes and the last cell is not always
    outside. Cells are wall, outside, unknown or a small room id, never `{"`, so the first `{"` starts
    the JSON. The rest is the library's `parse`.
    """

    def parse(self, raw: bytes, *args: Any, **kwargs: Any) -> MapData:
        map_info_start = raw.find(b'{"')
        map_info = json.loads(raw[map_info_start:])
        width = map_info["width"]
        height = map_info["height"]
        if map_info_start < width * height:
            raise ValueError("The map is shorter than its bitmap")
        map_image = raw[map_info_start - width * height:map_info_start]
        map_data = MapData(0, 1000)
        image, rooms = self._parse_image(map_image, width, height, map_info)
        map_data.image = image
        map_data.rooms = rooms
        map_data.path = RoidmiMapDataParser._parse_path(map_info)
        map_data.vacuum_position = RoidmiMapDataParser._parse_vacuum_position(map_info)
        map_data.charger = RoidmiMapDataParser._parse_charger_position(map_info)
        map_data.no_go_areas, map_data.no_mopping_areas, map_data.walls = RoidmiMapDataParser._parse_areas(map_info)
        if map_data.image is not None and not map_data.image.is_empty:
            self._image_generator.draw_map(map_data)
            if len(map_data.rooms) > 0 and map_data.vacuum_position is not None:
                map_data.vacuum_room = RoidmiMapDataParser._get_current_vacuum_room(map_image, map_data, width)
                if map_data.vacuum_room is not None:
                    map_data.vacuum_room_name = map_data.rooms[map_data.vacuum_room].name
        return map_data


class RoidmiCloudVacuum(BaseXiaomiCloudVacuumV2):
    _roidmi_map_data_parser: RoidmiMapDataParser

    def __init__(self: Self, vacuum_config: VacuumConfig) -> None:
        super().__init__(vacuum_config)
        self._run_state = CloudRunState(
            self._connector, self._server, self._device_id, ROIDMI_RUN_STATES.get(self.model))
        self._pro_map = self.model in PRO_MAP_MODELS
        parser_class = RoidmiProMapDataParser if self._pro_map else RoidmiMapDataParser
        self._roidmi_map_data_parser = parser_class(
            vacuum_config.palette,
            vacuum_config.sizes,
            vacuum_config.drawables,
            vacuum_config.image_config,
            vacuum_config.texts
        )

    async def should_update_map(self: Self) -> bool | None:
        return await self._run_state.should_update_map()

    async def get_map_url(self: Self, map_name: str) -> str | None:
        if not self._pro_map:
            return await super().get_map_url(map_name)
        url = await self.get_fallback_map_url(map_name)
        if url is None:
            url = await self.get_interim_file_url("/v2/home/get_interim_file_url", map_name)
        return url

    @staticmethod
    def vacuum_platform() -> VacuumApi:
        return VacuumApi.ROIDMI

    def map_archive_extension(self: Self) -> str:
        return "gz"

    @property
    def map_data_parser(self: Self) -> RoidmiMapDataParser:
        return self._roidmi_map_data_parser
