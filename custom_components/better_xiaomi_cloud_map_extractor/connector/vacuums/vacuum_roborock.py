import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Self

from miio import RoborockVacuum, DeviceException

from vacuum_map_parser_roborock.map_data_parser import RoborockMapDataParser
from .base.model import VacuumConfig, VacuumApi
from .base.vacuum_base import BaseXiaomiCloudVacuum
from ..utils.backoff import Backoff
from ..utils.exceptions import InvalidDeviceTokenException, SessionExpiredException
from ..utils.dict_operations import path_extractor

_LOGGER = logging.getLogger(__name__)

MISSING_MAP_VALUE = "retry"
OFF_UPDATES = 3
MAP_NAME_ATTEMPTS = 10
MOVING_STATES = frozenset({
    1,  # Starting
    4,  # Remote control active
    5,  # Cleaning
    6,  # Returning home
    7,  # Manual mode
    11,  # Spot cleaning
    15,  # Docking
    16,  # Going to target
    17,  # Zoned cleaning
    18,  # Segment cleaning
    26,  # Going to wash the mop
})


class _NoAnswer(Exception):
    """The vacuum did not answer, on the local network or through the cloud."""


def roborock_state(response: Any) -> int | None:
    """The state code of a `get_status` answer forwarded by the cloud (`[{"state": 8, ...}]`)."""
    result = path_extractor(response, "result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        return None
    state = result[0].get("state")
    return state if isinstance(state, int) and not isinstance(state, bool) else None


def roborock_map_name(response: Any) -> str | None:
    """The map name of a `get_map_v1` answer forwarded by the cloud (`["<map name>"]`)."""
    result = path_extractor(response, "result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], str):
        return None
    return result[0]


class RoborockCloudVacuum(BaseXiaomiCloudVacuum):
    """Roborock reads its map name and state on the local network (python-miio).

    When Home Assistant can't reach it there (another network, VLAN isolation, a changed IP), the same
    miIO commands go through the Xiaomi cloud (`/v2/home/rpc/<did>`), which forwards them to the vacuum
    over its own cloud connection, so the map keeps working. `lan_reachable` still reports the LAN.
    """

    _roborock_map_data_parser: RoborockMapDataParser
    _vacuum: RoborockVacuum
    _backoff: Backoff
    _off_counter: int

    def __init__(self: Self, vacuum_config: VacuumConfig) -> None:
        super().__init__(vacuum_config)
        self._roborock_map_data_parser = RoborockMapDataParser(
            vacuum_config.palette,
            vacuum_config.sizes,
            vacuum_config.drawables,
            vacuum_config.image_config,
            vacuum_config.texts
        )
        self._vacuum = RoborockVacuum(vacuum_config.host, vacuum_config.token)
        self._backoff = Backoff(0.2, 15)
        self._off_counter = 0

    async def get_map_url(self: Self, map_name: str) -> str | None:
        url = self._connector.get_api_url(self._server) + "/home/getmapfileurl"
        params = {
            "data": '{"obj_name":"' + map_name + '"}'
        }
        api_response = await self._connector.execute_api_call_encrypted(url, params)
        return path_extractor(api_response, "result.url")

    async def should_update_map(self: Self) -> bool | None:
        try:
            code = (await asyncio.to_thread(self._vacuum.status)).state_code
            self.lan_reachable = True
        except DeviceException as de:
            if "token" in repr(de):
                raise InvalidDeviceTokenException()
            self.lan_reachable = False
            code = await self._cloud_state()
            if code is None:
                # Neither tells: the connector checks the map for changes less often instead.
                return None
        _LOGGER.debug("Vacuum status: %d", code)
        if code in MOVING_STATES:
            self._off_counter = 0
            _LOGGER.debug("Vacuum is moving. Updating map.")
            return True
        self._off_counter += 1
        _LOGGER.debug("Vacuum is not moving. Off counter: %d", self._off_counter)
        return self._off_counter <= OFF_UPDATES

    async def _cloud_state(self: Self) -> int | None:
        try:
            response = await self._cloud_rpc("get_status")
        except Exception as ex:  # the map download that may follow handles the session and reports failures
            _LOGGER.debug("Could not read the state through the cloud: %r", ex)
            return None
        code = roborock_state(response)
        if code is None:
            _LOGGER.debug("The cloud did not report a state (code %s)",
                          response.get("code") if isinstance(response, dict) else None)
        return code

    async def _cloud_rpc(self: Self, method: str) -> Any:
        return await self._connector.get_other_info(str(self._device_id), method, [], server=self._server)

    @staticmethod
    def vacuum_platform() -> VacuumApi:
        return VacuumApi.ROBOROCK

    @property
    def map_archive_extension(self: Self) -> str:
        return "gz"

    @property
    def map_data_parser(self: Self) -> RoborockMapDataParser:
        return self._roborock_map_data_parser

    async def get_map_name(self: Self) -> str:
        # The LAN first, unless the vacuum did not answer there at the last status check; a source that
        # does not answer hands over to the other one right away. "retry" means the vacuum is still
        # uploading the map: ask the same source again after a backoff.
        sources: list[tuple[str, Callable[[], Awaitable[str]]]] = [
            ("local network", self._map_name_from_lan), ("cloud", self._map_name_from_cloud)]
        if self.lan_reachable is False:
            sources.reverse()
        self._backoff.reset()
        failed_in_a_row = 0
        for remaining_attempts in range(MAP_NAME_ATTEMPTS - 1, -1, -1):
            source, read = sources[0]
            _LOGGER.debug("Retrieving map name through the %s, remaining_attempts: %d", source, remaining_attempts)
            try:
                map_name = await read()
            except _NoAnswer as exc:
                _LOGGER.debug("No map name through the %s: %s", source, exc)
                sources.append(sources.pop(0))
                failed_in_a_row += 1
                if failed_in_a_row < len(sources):
                    continue
            else:
                failed_in_a_row = 0
                _LOGGER.debug("Map name %s", map_name)
                if map_name != MISSING_MAP_VALUE:
                    return map_name
            if remaining_attempts > 0:
                await self._backoff.sleep()
        return MISSING_MAP_VALUE

    async def _map_name_from_lan(self: Self) -> str:
        try:
            return (await asyncio.to_thread(self._vacuum.map))[0]
        except (OSError, DeviceException) as exc:
            raise _NoAnswer(repr(exc)) from exc

    async def _map_name_from_cloud(self: Self) -> str:
        try:
            response = await self._cloud_rpc("get_map_v1")
        except SessionExpiredException:
            raise  # the connector renews the session and downloads again
        except Exception as exc:
            raise _NoAnswer(repr(exc)) from exc
        map_name = roborock_map_name(response)
        if map_name is None:
            raise _NoAnswer(f"code {response.get('code') if isinstance(response, dict) else None}")
        return map_name
