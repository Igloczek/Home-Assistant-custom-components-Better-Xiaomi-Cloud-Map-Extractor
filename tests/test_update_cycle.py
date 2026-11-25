"""The map update cycle inside Home Assistant, for every vendor.

Unlike the other setup tests, nothing between Home Assistant and the network is mocked: the entry
sets up with the real connector and vendor class, the Xiaomi cloud is `FakeXiaomiCloud`, and the
vacuum on the local network is `FakeLanVacuum` under the real python-miio. That is what lets
Home Assistant's own blocking-call detection see a python-miio call made on the event loop
(upstream #775, #769), and what lets these tests count the map downloads and state writes a docked
vacuum causes (upstream #764, #719).
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.vacuum_base import (
    BaseXiaomiCloudVacuum,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_dreame import DreameCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_roborock import (
    RoborockCloudVacuum,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_roidmi import RoidmiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.vacuum_viomi import ViomiCloudVacuum
from custom_components.better_xiaomi_cloud_map_extractor.const import (
    CONF_SERVER,
    CONF_USED_MAP_API,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.better_xiaomi_cloud_map_extractor.repairs import LAN_UNREACHABLE_UPDATES

from .const import ENTRY_DATA, MAC, TOKEN
from .fake_miio import FakeLanVacuum, lan_vacuum
from .fake_xiaomi import USER_ID, FakeResponse, FakeXiaomiCloud
from .ijai_map import DEVICE_ID, WIFI_SN, build_map, encrypt_map, fixture
from .test_session_lifecycle import stored_session
from .test_vacuum_xiaomi import MAP_JSON, encrypt_xiaomi_map

MAP_URL = "https://maps.example.com/map/0"
IMAGE = "image.living_room_vacuum_live_map"
BLOCKING_CALL = "Detected blocking call"
NO_MAPPING = "Neither the class nor the parameter defines the mapping"

# Vendors whose maps the tests cannot build stand in a map the parser draws itself, one image per
# map content, so a changed map still looks changed.
PARSER_DRAWN = (ViomiCloudVacuum, RoidmiCloudVacuum, DreameCloudVacuum, RoborockCloudVacuum)


@dataclass(frozen=True)
class Vendor:
    model: str
    api: str
    # MIoT properties the vacuum reports while docked and while cleaning.
    docked: dict[tuple[int, int], object]
    cleaning: dict[tuple[int, int], object]
    # Builds the map the cloud serves; `version` makes a different map.
    raw_map: Callable[[int], bytes]

    def __str__(self) -> str:
        return self.model


def _xiaomi_map(model: str) -> Callable[[int], bytes]:
    def build(version: int) -> bytes:
        data = json.loads(MAP_JSON)
        data["version"] = version  # any change to the map JSON changes the upload
        return encrypt_xiaomi_map(json.dumps(data), model, DEVICE_ID)

    return build


def _ijai_map(model: str) -> Callable[[int], bytes]:
    return lambda version: encrypt_map(model, owner_id=USER_ID, robot_map=build_map(map_id=version + 1))


def _opaque_map(version: int) -> bytes:
    return f"map version {version}".encode()


OBJ_NAME = {(10, 1): json.dumps({"obj_name": f"{USER_ID}/{DEVICE_ID}/0"})}
IJAI_SN = {(1, 3): WIFI_SN}

VENDORS = [
    Vendor("viomi.vacuum.v8", "VIOMI", {}, {}, _opaque_map),
    Vendor("roidmi.vacuum.v60", "ROIDMI", {}, {}, _opaque_map),
    Vendor("dreame.vacuum.p2008", "DREAME", {}, {}, _opaque_map),
    # Roborock reports its state through miIO `get_status`, set on FakeLanVacuum.
    Vendor("roborock.vacuum.s5", "ROBOROCK", {}, {}, _opaque_map),
    Vendor("ijai.vacuum.v3", "IJAI", {**IJAI_SN, (2, 1): 2}, {**IJAI_SN, (2, 1): 5}, _ijai_map("ijai.vacuum.v3")),
    Vendor(
        "xiaomi.vacuum.ov81gl", "XIAOMI", {**OBJ_NAME, (2, 1): 2}, {**OBJ_NAME, (2, 1): 5},
        _xiaomi_map("xiaomi.vacuum.ov81gl"),
    ),
    # Upstream #764: on the H50, 2/1 is the firmware version, a string; the status is 2/2.
    Vendor(
        "xiaomi.vacuum.ov43gb", "XIAOMI", {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 9},
        {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 1}, _xiaomi_map("xiaomi.vacuum.ov43gb"),
    ),
]
UNSUPPORTED = Vendor("unknown.vacuum.x1", "UNSUPPORTED", {}, {}, _opaque_map)


class VacuumHome:
    """The Xiaomi cloud and the vacuum on the LAN for one config entry."""

    def __init__(self, vendor: Vendor) -> None:
        self.vendor = vendor
        self.map_version = 0
        self.lan = FakeLanVacuum(vendor.model, properties=dict(vendor.docked))
        self.cloud = FakeXiaomiCloud()
        self.cloud.valid_service_tokens.add("stored-service-token")
        self.cloud.encrypted_api(
            "/v2/homeroom/gethome", {"result": {"homelist": [{"id": "1", "uid": int(USER_ID)}]}}
        )
        self.cloud.encrypted_api(
            "/v2/home/home_device_list",
            {
                "result": {
                    "device_info": [
                        {
                            "did": DEVICE_ID,
                            "name": "Living room vacuum",
                            "model": vendor.model,
                            "token": TOKEN,
                            "localip": "192.168.1.50",
                            "mac": MAC.upper(),
                        }
                    ]
                }
            },
        )
        url = {"result": {"url": f"{MAP_URL}?Expires=1"}}
        for endpoint in ("/v2/home/get_interim_file_url", "/v2/home/get_interim_file_url_pro", "/home/getmapfileurl"):
            self.cloud.encrypted_api(endpoint, url)
        self.cloud.encrypted_api(f"/v2/home/rpc/{DEVICE_ID}", fixture("cloud_responses.json")["get_properties_unknown"])
        self.cloud.on("GET", MAP_URL, lambda _request: FakeResponse(body=vendor.raw_map(self.map_version)))

    @property
    def downloads(self) -> int:
        return len(self.cloud.calls(MAP_URL))

    def start_cleaning(self) -> None:
        self.lan.properties = dict(self.vendor.cleaning)
        self.lan.roborock_state = 5

    def dock(self) -> None:
        self.lan.properties = dict(self.vendor.docked)
        self.lan.roborock_state = 8


def _draw_map(self: BaseXiaomiCloudVacuum, raw_map: bytes):
    return self.map_data_parser.create_empty(raw_map.decode())


@pytest.fixture
def setup_vacuum(hass: HomeAssistant) -> Iterator[Callable[[Vendor], "AsyncVacuum"]]:
    homes: list[VacuumHome] = []

    def make(vendor: Vendor) -> AsyncVacuum:
        home = VacuumHome(vendor)
        homes.append(home)
        return AsyncVacuum(hass, home)

    with (
        patch(
            "custom_components.better_xiaomi_cloud_map_extractor.async_create_clientsession",
            side_effect=lambda _hass: homes[-1].cloud.session_creator(),
        ),
        patch(
            "custom_components.better_xiaomi_cloud_map_extractor.restore_connector_config",
            AsyncMock(side_effect=lambda *_: stored_session(expires_in=datetime.timedelta(days=20))),
        ),
        patch("custom_components.better_xiaomi_cloud_map_extractor.save_connector_config", AsyncMock()),
    ):
        patches = [patch.object(cls, "decode_and_parse", _draw_map) for cls in PARSER_DRAWN]
        for active in patches:
            active.start()
        yield make
        for active in patches:
            active.stop()


class AsyncVacuum:
    def __init__(self, hass: HomeAssistant, home: VacuumHome) -> None:
        self.hass = hass
        self.home = home
        flow = XiaomiCloudMapExtractorFlowHandler
        self.entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=MAC,
            title="Living room vacuum",
            data={
                **ENTRY_DATA,
                "device_id": DEVICE_ID,
                "model": home.vendor.model,
                CONF_USED_MAP_API: home.vendor.api,
                CONF_SERVER: "de",
            },
            options={
                "image_config": flow._default_image_config(),
                "colors": flow._default_colors(),
                "room_colors": {},
                "drawables": ["charger", "path"],
                "sizes": {},
                "texts": [],
            },
        )

    async def setup(self) -> None:
        self.entry.add_to_hass(self.hass)
        with lan_vacuum(self.home.lan):
            assert await self.hass.config_entries.async_setup(self.entry.entry_id)
            await self.hass.async_block_till_done()
        assert self.entry.state is ConfigEntryState.LOADED

    async def cycles(self, freezer: FrozenDateTimeFactory, count: int = 1) -> None:
        coordinator = self.entry.runtime_data.coordinator
        with lan_vacuum(self.home.lan):
            for _ in range(count):
                freezer.tick(DEFAULT_UPDATE_INTERVAL)
                await coordinator.async_refresh()
                await self.hass.async_block_till_done()
                assert coordinator.last_update_success, coordinator.last_exception


def _ids(vendors: list[Vendor]) -> list[str]:
    return [vendor.model for vendor in vendors]


# --- the event loop ------------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", [*VENDORS, UNSUPPORTED], ids=_ids([*VENDORS, UNSUPPORTED]))
async def test_update_cycle_never_blocks_the_event_loop(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture,
    vendor: Vendor,
) -> None:
    """Setup, docked cycles, cleaning cycles and a forced refresh: no vacuum call on the event loop."""
    vacuum = setup_vacuum(vendor)

    await vacuum.setup()
    await vacuum.cycles(freezer, 2)
    vacuum.home.start_cleaning()
    vacuum.home.map_version += 1
    await vacuum.cycles(freezer, 2)
    with lan_vacuum(vacuum.home.lan):
        await vacuum.entry.runtime_data.coordinator.force_update_data()
        await hass.async_block_till_done()

    assert BLOCKING_CALL not in caplog.text
    assert vacuum.home.lan.calls_on_event_loop == []
    assert vacuum.home.downloads >= 2


@pytest.mark.parametrize("vendor", [v for v in VENDORS if v.api in ("XIAOMI", "IJAI")], ids=str)
async def test_miot_device_needs_no_mapping(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture,
    vendor: Vendor,
) -> None:
    """Upstream #791: "Neither the class nor the parameter defines the mapping" at every start."""
    caplog.set_level(logging.WARNING, logger="miio")
    vacuum = setup_vacuum(vendor)

    await vacuum.setup()

    assert NO_MAPPING not in caplog.text


# --- map downloads -------------------------------------------------------------------------------

DOCKED_MINUTES = 5
CYCLES_PER_MINUTE = int(datetime.timedelta(minutes=1) / DEFAULT_UPDATE_INTERVAL)


@pytest.mark.parametrize("vendor", VENDORS, ids=str)
async def test_docked_vacuum_stops_downloading_an_unchanged_map(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """Upstream #764: a docked vacuum's map was downloaded and parsed every 10 s, forever."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE)  # settle: a few more downloads after docking

    before = vacuum.home.downloads
    await vacuum.cycles(freezer, DOCKED_MINUTES * CYCLES_PER_MINUTE)

    # At most a check per minute (vendors that cannot tell docked from cleaning), not every 10 s.
    assert vacuum.home.downloads - before <= DOCKED_MINUTES


@pytest.mark.parametrize("vendor", VENDORS, ids=str)
async def test_cleaning_brings_back_a_download_every_cycle(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    await vacuum.cycles(freezer, 2 * CYCLES_PER_MINUTE)

    vacuum.home.start_cleaning()
    vacuum.home.cloud.on(
        "GET", MAP_URL, lambda _r: FakeResponse(body=vendor.raw_map(vacuum.home.downloads + 100))
    )
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE)  # noticed within a minute at the latest

    before = vacuum.home.downloads
    await vacuum.cycles(freezer, 3)
    assert vacuum.home.downloads - before == 3


@pytest.mark.parametrize(
    "vendor", [v for v in VENDORS if v.api == "IJAI"],
    ids=str,
)
async def test_vacuum_unreachable_on_the_lan_still_gets_new_maps(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """Ijai reads its status over the LAN but downloads the map from the cloud: without the status the
    map still follows the cloud, where it used to stop updating."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    vacuum.home.lan.reachable = False
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE)
    shown = hass.states.get(IMAGE).state

    vacuum.home.map_version += 1
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE + 1)

    assert hass.states.get(IMAGE).state != shown


async def test_first_map_is_retried_while_docked(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory
) -> None:
    """A docked vacuum whose first map failed to download keeps trying until there is a map."""
    vendor = next(v for v in VENDORS if v.model == "xiaomi.vacuum.ov81gl")
    vacuum = setup_vacuum(vendor)
    vacuum.home.cloud.on("GET", MAP_URL, lambda _r: FakeResponse(status=404, body=b""))
    await vacuum.setup()
    await vacuum.cycles(freezer, 6)

    vacuum.home.cloud.on("GET", MAP_URL, lambda _r: FakeResponse(body=vendor.raw_map(0)))
    await vacuum.cycles(freezer, 1)

    assert vacuum.entry.runtime_data.coordinator.data.map_data is not None


# --- the vacuum on the local network --------------------------------------------------------------

# Vendors that read the vacuum's status on the local network; the others only talk to the cloud.
LAN_STATUS = [v for v in VENDORS if v.api in ("ROBOROCK", "IJAI", "XIAOMI")]
CLOUD_ONLY = [v for v in VENDORS if v not in LAN_STATUS]


def _lan_issue(hass: HomeAssistant, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"vacuum_unreachable_{entry_id}")


@pytest.mark.parametrize("vendor", LAN_STATUS, ids=str)
async def test_vacuum_unreachable_on_the_lan_raises_a_repair_until_it_answers(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """A vacuum that stops answering on the LAN (new IP address, new token after a reset) used to fail
    quietly: the map froze or went cloud-only and only the debug log said why."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    vacuum.home.lan.reachable = False

    await vacuum.cycles(freezer, LAN_UNREACHABLE_UPDATES - 1)
    assert _lan_issue(hass, vacuum.entry.entry_id) is None, "a short Wi-Fi drop is not worth a repair"

    await vacuum.cycles(freezer, 1)
    issue = _lan_issue(hass, vacuum.entry.entry_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["host"] == vacuum.entry.data["host"]

    vacuum.home.lan.reachable = True
    await vacuum.cycles(freezer, 1)
    assert _lan_issue(hass, vacuum.entry.entry_id) is None


@pytest.mark.parametrize("vendor", CLOUD_ONLY, ids=str)
async def test_cloud_only_vendors_never_raise_the_lan_repair(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    vacuum.home.lan.reachable = False

    await vacuum.cycles(freezer, LAN_UNREACHABLE_UPDATES + 1)

    assert _lan_issue(hass, vacuum.entry.entry_id) is None


# --- state writes --------------------------------------------------------------------------------


def _domain_entities(hass: HomeAssistant) -> set[str]:
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    return {entry.entity_id for entry in er.async_entries_for_config_entry(er.async_get(hass), entry_id)}


@pytest.mark.parametrize("vendor", VENDORS, ids=str)
async def test_unchanged_map_writes_no_state(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """Upstream #719: every update wrote new states (logbook, recorder) although nothing changed."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    entities = _domain_entities(hass)
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)

    await vacuum.cycles(freezer, 3 * CYCLES_PER_MINUTE)

    assert [e.data["entity_id"] for e in changes if e.data["entity_id"] in entities] == []


@pytest.mark.parametrize("vendor", VENDORS, ids=str)
async def test_changed_map_updates_the_image_once(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    shown = hass.states.get(IMAGE).state
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)

    vacuum.home.start_cleaning()
    vacuum.home.map_version += 1
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE + 3)

    assert hass.states.get(IMAGE).state != shown
    assert [e.data["entity_id"] for e in changes].count(IMAGE) == 1


async def test_auto_update_switch_shows_its_state(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory
) -> None:
    vacuum = setup_vacuum(VENDORS[0])
    await vacuum.setup()
    registry = er.async_get(hass)
    switch = next(e for e in _domain_entities(hass) if e.startswith("switch."))
    registry.async_update_entity(switch, disabled_by=None)
    with lan_vacuum(vacuum.home.lan):
        await hass.config_entries.async_reload(vacuum.entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
        await hass.async_block_till_done()

    assert hass.states.get(switch).state == "off"
