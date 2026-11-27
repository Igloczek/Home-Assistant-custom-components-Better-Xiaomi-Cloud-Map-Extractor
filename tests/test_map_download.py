"""Downloading the map file from the address the cloud hands out.

A download that hangs or breaks off is a failed download (the last map stays, the next update tries
again), never an aiohttp error reaching the coordinator, and it never waits aiohttp's five minutes.
"""

from __future__ import annotations


from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

from .fake_xiaomi import FakeResponse, FakeXiaomiCloud

MAP_URL = "https://maps.example.invalid/map_0"


async def _connector(cloud: FakeXiaomiCloud) -> XiaomiCloudConnector:
    connector = XiaomiCloudConnector(cloud.session_creator, server="de")
    await connector.create_session()
    return connector


async def test_map_download_has_a_timeout() -> None:
    cloud = FakeXiaomiCloud()
    cloud.on("GET", MAP_URL, lambda r: FakeResponse(body=b"map"))
    connector = await _connector(cloud)

    assert await connector.get_raw_map_data(MAP_URL) == b"map"
    timeout = cloud.calls(MAP_URL)[0].timeout
    assert timeout is not None and timeout.total is not None and timeout.total <= 120


async def test_map_download_breaking_off_while_reading_is_a_failed_download() -> None:
    class BrokenOff(FakeResponse):
        async def read(self) -> bytes:
            raise TimeoutError

    cloud = FakeXiaomiCloud()
    cloud.on("GET", MAP_URL, lambda r: BrokenOff(body=b""))
    connector = await _connector(cloud)

    assert await connector.get_raw_map_data(MAP_URL) is None
