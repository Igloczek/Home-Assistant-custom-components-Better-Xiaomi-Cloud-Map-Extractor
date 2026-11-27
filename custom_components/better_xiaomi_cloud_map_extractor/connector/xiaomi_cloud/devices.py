"""Homes and devices of the account, in one region or in every region."""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Self

from .api import EncryptedApi
from .const import AVAILABLE_SERVERS
from ..utils.dict_operations import path_extractor
from ..utils.exceptions import FailedConnectionException, SessionExpiredException, UnexpectedResponseError

# One logger for the whole cloud connector, named as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.connector")


@dataclass
class XiaomiCloudHome:
    home_id: int
    owner: int


@dataclass
class XiaomiCloudDeviceInfo:
    device_id: str
    name: str
    model: str
    token: str
    spec_type: str
    local_ip: str | None
    mac: str | None
    server: str
    home_id: int
    user_id: int



class DeviceDiscovery(EncryptedApi):

    async def _get_homes(self: Self, server: str, strict: bool = False) -> list[XiaomiCloudHome]:
        url = self.get_api_url(server) + "/v2/homeroom/gethome"
        params = {
            "data": '{"fg": true, "fetch_share": true, "fetch_share_dev": true, "limit": 300, "app_ver": 7}'
        }

        homes = []
        try:
            homes_response = await self.execute_api_call_encrypted(url, params)
        except Exception as e:
            if strict:
                raise
            _LOGGER.debug("Error when retrieving homes from server %s: %s", server, e)
            homes_response = None
        if homes_response is None:
            return homes
        if homes_list := path_extractor(homes_response, "result.homelist"):
            homes.extend([XiaomiCloudHome(int(home["id"]), home["uid"]) for home in homes_list])
        if homes_list := path_extractor(homes_response, "result.share_home_list"):
            homes.extend([XiaomiCloudHome(int(home["id"]), home["uid"]) for home in homes_list])
        return homes

    async def _get_devices_from_home(
        self: Self, server: str, home_id: int, owner_id: int, strict: bool = False
    ) -> list[XiaomiCloudDeviceInfo]:
        url = self.get_api_url(server) + "/v2/home/home_device_list"
        params = {
            "data": json.dumps(
                {
                    "home_id": home_id,
                    "home_owner": owner_id,
                    "limit": 200,
                    "get_split_device": True,
                    "support_smart_home": True,
                }
            )
        }
        try:
            if (response := await self.execute_api_call_encrypted(url, params)) is None:
                return []
        except (FailedConnectionException, TimeoutError) as e:
            if strict:
                raise
            _LOGGER.warning("Failed to get devices from home: %s", e)
            return []

        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            # Not "no devices": Xiaomi answered without the list, so this home can't be trusted as empty.
            raise FailedConnectionException(UnexpectedResponseError(200))
        if (raw_devices := result.get("device_info")) is None:
            return []
        return [
            XiaomiCloudDeviceInfo(
                device_id=device["did"],
                name=device["name"],
                model=device["model"],
                token=device.get("token", ""),
                spec_type=device.get("spec_type", ""),
                local_ip=device.get("localip", None),
                mac=device.get("mac", None),
                server=server,
                user_id=owner_id,
                home_id=home_id,
            )
            for device in raw_devices
        ]

    async def get_devices(self: Self, server: str | None = None) -> list[XiaomiCloudDeviceInfo]:
        """The account's devices on one server, or on every server when none is given.

        Errors always reach the caller for a single server (map updates): a timeout or an expired
        session is not "this account has no such device". Across every server (setup), a region that
        fails is skipped, but only while another one lists devices: an empty list built from failed
        regions would read as "no vacuums in this account".
        """
        if server is not None:
            return await self._get_devices_from_server(server, strict=True)
        results = await asyncio.gather(
            *(self._get_devices_from_server(country, strict=True) for country in AVAILABLE_SERVERS),
            return_exceptions=True,
        )
        devices = [device for result in results if isinstance(result, list) for device in result]
        failures = [result for result in results if isinstance(result, BaseException)]
        for failure in failures:
            if not isinstance(failure, Exception):
                raise failure
            _LOGGER.debug("Device list of a region failed: %s", type(failure).__name__)
        if devices or not failures:
            return devices
        if session_error := next((f for f in failures if isinstance(f, SessionExpiredException)), None):
            raise session_error
        if connection_error := next((f for f in failures if isinstance(f, FailedConnectionException)), None):
            raise connection_error
        raise FailedConnectionException(failures[0])

    async def _get_devices_from_server(self: Self, server: str, strict: bool = False) -> list[XiaomiCloudDeviceInfo]:
        homes = await self._get_homes(server, strict)
        device_coro_list = [self._get_devices_from_home(server, home.home_id, home.owner, strict) for home in homes]
        device_lists = await asyncio.gather(*device_coro_list)
        return [device for device_list in device_lists for device in device_list]

    async def get_device_details(self: Self, device_id: str, server: str | None = None) -> XiaomiCloudDeviceInfo | None:
        devices = await self.get_devices(server)
        matching_device = filter(lambda device: device.device_id == device_id, devices)
        return next(matching_device, None)
