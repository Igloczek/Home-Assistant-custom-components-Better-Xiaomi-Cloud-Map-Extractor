"""Ijai vacuums: map download, WiFi serial number and map decryption.

The cloud answers with the responses recorded in tests/fixtures/ijai, the vacuum on the local
network with the recorded property answers, and the map is built and encrypted by
tests/ijai_map.py the way the vacuum uploads it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from miio.exceptions import DeviceException
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes

from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.model import VacuumConfig
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai import IjaiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import (
    XiaomiCloudConnector,
    XiaomiCloudDeviceInfo,
)

from .const import TOKEN
from .ijai_map import (
    DEVICE_ID,
    MAC,
    OWNER_ID,
    encrypt_map,
    fixture,
)

API = "https://de.api.io.mi.com/app"
STANDARD_URL = API + "/v2/home/get_interim_file_url"
PRO_URL = API + "/v2/home/get_interim_file_url_pro"
ACTION_URL = API + "/v2/miotspec/action"
MAP_URL = fixture("cloud_responses.json")["interim_file_url"]["result"]["url"]

CLOUD = fixture("cloud_responses.json")
LOCAL = fixture("local_properties.json")
UNREACHABLE = "unreachable"


class FakeCloud:
    """The Xiaomi cloud as the vacuum class sees it, answering with recorded responses."""

    def __init__(self, raw_map: bytes, urls: dict[str, Any], properties: Any, logged_in_id: str) -> None:
        self.connector = MagicMock(spec=XiaomiCloudConnector)
        self.connector.get_api_url = MagicMock(return_value=API)
        self.connector.execute_api_call_encrypted = AsyncMock(side_effect=self._call)
        self.connector.get_raw_map_data = AsyncMock(
            side_effect=lambda url: self.raw_map if url == MAP_URL else None
        )
        self.connector.get_other_info = AsyncMock(return_value=properties)
        self.connector.to_config = MagicMock(return_value=MagicMock(user_id=logged_in_id))
        self.raw_map = raw_map
        self.urls = urls
        self.calls: list[str] = []

    async def _call(self, url: str, params: dict[str, str]) -> Any:
        self.calls.append(url)
        if url == ACTION_URL:
            return {"code": 0, "message": "ok", "result": {"code": 0}}
        return self.urls[url]


@pytest.fixture
def ijai() -> Callable[..., tuple[IjaiCloudVacuum, FakeCloud]]:
    """Create an Ijai vacuum against the fake cloud and a vacuum answering on the local network."""
    patches = []

    def make(
        model: str,
        raw_map: bytes | None = None,
        *,
        local: str = "healthy",
        urls: dict[str, Any] | None = None,
        cloud_properties: Any = None,
        logged_in_id: str = OWNER_ID,
    ) -> tuple[IjaiCloudVacuum, FakeCloud]:
        cloud = FakeCloud(
            encrypt_map(model) if raw_map is None else raw_map,
            urls or {STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url"]},
            cloud_properties or CLOUD["get_properties_unknown"],
            logged_in_id,
        )
        miot_patch = patch(
            "custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_ijai.MiotDevice"
        )
        miot = miot_patch.start().return_value
        patches.append(miot_patch)
        if local == UNREACHABLE:
            miot.get_property_by.side_effect = DeviceException("Unable to discover the device 192.0.2.10")
        else:
            miot.get_property_by.side_effect = lambda siid, piid: LOCAL[local][f"{siid}-{piid}"]
        device_info = XiaomiCloudDeviceInfo(
            device_id=DEVICE_ID,
            name="Vacuum",
            model=model,
            token=TOKEN,
            spec_type="",
            local_ip="192.0.2.10",
            mac=MAC,
            server="de",
            home_id=1,
            user_id=int(OWNER_ID),
        )
        config = VacuumConfig(
            cloud.connector, device_info, "de", DEVICE_ID, "192.0.2.10", TOKEN, model,
            ColorsPalette(), [], ImageConfig(), Sizes({}), [],
        )
        return IjaiCloudVacuum(config), cloud

    yield make
    for active in patches:
        active.stop()


# Map download --------------------------------------------------------------------------------


async def test_non_k3_model_uses_the_standard_endpoint(ijai) -> None:
    """Models outside the Mi Home plugin's K3 list read the standard endpoint (upstream PR #725)."""
    vacuum, cloud = ijai(
        "ijai.vacuum.v10",
        urls={STANDARD_URL: CLOUD["interim_file_url"], PRO_URL: CLOUD["interim_file_url_pro_no_storage"]},
    )

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
    assert cloud.calls == [STANDARD_URL]


async def test_k3_model_uses_the_pro_endpoint(ijai) -> None:
    vacuum, cloud = ijai("ijai.vacuum.v17")

    await vacuum.get_map()

    assert cloud.calls == [PRO_URL]


# Map key -------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["ijai.vacuum.v10", "xiaomi.vacuum.d106gl"])
async def test_models_without_a_hex_key(ijai, model: str) -> None:
    """Only some models use the MD5 digest itself as the key (upstream PR #725, "isEncryptKeyTypeHex")."""
    vacuum, _ = ijai(model)

    map_data, _ = await vacuum.get_map()

    assert map_data.image is not None
