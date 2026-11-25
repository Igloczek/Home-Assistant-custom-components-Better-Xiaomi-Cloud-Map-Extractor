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
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.connector.vacuums.base.vacuum_base import (
    BaseXiaomiCloudVacuum,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.backoff import Backoff
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

from .const import ENTRY_DATA, HOST, MAC, TOKEN
from .fake_miio import FakeLanVacuum, lan_vacuum
from .fake_xiaomi import API, USER_ID, FakeResponse, FakeXiaomiCloud, bad_gateway, connection_timeout
from .ijai_map import DEVICE_ID, WIFI_SN, build_map, encrypt_map, map_key
from .log_secrets import MAP_URL_QUERY, assert_no_secrets, leaked
from .test_session_lifecycle import stored_session
from .test_vacuum_xiaomi import MAP_JSON, app_key, encrypt_xiaomi_map

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
    # Properties the vacuum reports while docked and while cleaning: MIoT by (siid, piid), miIO by name.
    docked: dict[tuple[int, int] | str, object]
    cleaning: dict[tuple[int, int] | str, object]
    # Builds the map the cloud serves; `version` makes a different map.
    raw_map: Callable[[int], bytes]

    @property
    def secrets(self) -> dict[str, str]:
        """The map decryption key, for the vendors that have one: it must not be logged."""
        if self.api == "XIAOMI":
            return {"map key": app_key(self.model, DEVICE_ID).hex()}
        if self.api == "IJAI":
            return {"map key": map_key(self.model, WIFI_SN, USER_ID, DEVICE_ID, MAC).hex()}
        return {}

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
    # Viomi, Roidmi and Dreame report their state only through the cloud (python-miio mappings):
    # Viomi miIO `run_state` 5 docked, 3 cleaning; Roidmi 2/1 6 charging, 4 sweeping; Dreame 2/1 6
    # charging, 1 sweeping.
    Vendor("viomi.vacuum.v8", "VIOMI", {"run_state": 5}, {"run_state": 3}, _opaque_map),
    Vendor("roidmi.vacuum.v60", "ROIDMI", {(2, 1): 6}, {(2, 1): 4}, _opaque_map),
    Vendor("dreame.vacuum.p2008", "DREAME", {(2, 1): 6}, {(2, 1): 1}, _opaque_map),
    # A model with no known state property: the map tells whether it moves.
    Vendor("viomi.vacuum.x1", "VIOMI", {}, {}, _opaque_map),
    # Roborock reports its state through miIO `get_status`, set on FakeLanVacuum.
    Vendor("roborock.vacuum.s5", "ROBOROCK", {}, {}, _opaque_map),
    Vendor("ijai.vacuum.v3", "IJAI", {**IJAI_SN, (2, 1): 2}, {**IJAI_SN, (2, 1): 5}, _ijai_map("ijai.vacuum.v3")),
    # Statuses where miot_spec.json has them: on the S40 and the H50, 2/1 is the firmware version and the
    # status is 2/2 (upstream #764); on the S20+ it is 2/1, where 4 is "Sweeping" (upstream PR #781).
    Vendor(
        "xiaomi.vacuum.ov81gl", "XIAOMI", {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 2},
        {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 4}, _xiaomi_map("xiaomi.vacuum.ov81gl"),
    ),
    Vendor(
        "xiaomi.vacuum.ov43gb", "XIAOMI", {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 9},
        {**OBJ_NAME, (2, 1): "4.3.3_0016", (2, 2): 16}, _xiaomi_map("xiaomi.vacuum.ov43gb"),
    ),
    Vendor(
        "xiaomi.vacuum.b108gl", "XIAOMI", {(7, 1): OBJ_NAME[(10, 1)], (2, 1): 2},
        {(7, 1): OBJ_NAME[(10, 1)], (2, 1): 4}, _xiaomi_map("xiaomi.vacuum.b108gl"),
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
        url = {"result": {"url": f"{MAP_URL}?{MAP_URL_QUERY}"}}
        for endpoint in ("/v2/home/get_interim_file_url", "/v2/home/get_interim_file_url_pro", "/home/getmapfileurl"):
            self.cloud.encrypted_api(endpoint, url)
        self.cloud.encrypted_rpc(f"/v2/home/rpc/{DEVICE_ID}", self.lan.cloud_rpc)
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
def setup_vacuum(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> Iterator[Callable[[Vendor], "AsyncVacuum"]]:
    """Every test here runs at DEBUG and ends with a scan: no secret in the log or a failure message."""
    caplog.set_level(logging.DEBUG)
    vacuums: list[AsyncVacuum] = []

    def make(vendor: Vendor, drawables: list[str] | None = None) -> AsyncVacuum:
        vacuum = AsyncVacuum(hass, VacuumHome(vendor), drawables)
        vacuums.append(vacuum)
        return vacuum

    homes = _Homes(vacuums)

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
    for vacuum in vacuums:
        assert_no_secrets(caplog, vacuum.home.vendor.secrets, *vacuum.failure_messages)


class _Homes:
    """The cloud of the vacuum set up last, for the patched client session."""

    def __init__(self, vacuums: list["AsyncVacuum"]) -> None:
        self._vacuums = vacuums

    def __getitem__(self, index: int) -> VacuumHome:
        return self._vacuums[index].home


class AsyncVacuum:
    def __init__(self, hass: HomeAssistant, home: VacuumHome, drawables: list[str] | None = None) -> None:
        self.hass = hass
        self.home = home
        # What Home Assistant showed for each failed update ("Failed to update", the entity's error).
        self.failure_messages: list[str] = []
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
                "drawables": drawables or ["charger", "path"],
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

    async def cycles(self, freezer: FrozenDateTimeFactory, count: int = 1, *, failing: bool | None = False) -> None:
        """`failing`: whether every update fails (True), succeeds (False), or either (None)."""
        coordinator = self.entry.runtime_data.coordinator
        with lan_vacuum(self.home.lan):
            for _ in range(count):
                freezer.tick(DEFAULT_UPDATE_INTERVAL)
                await coordinator.async_refresh()
                await self.hass.async_block_till_done()
                if coordinator.last_exception is not None:
                    self.failure_messages.append(str(coordinator.last_exception))
                if failing is not None:
                    assert coordinator.last_update_success is not failing, coordinator.last_exception


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


# Vendors that report whether they move; for the others the map is checked once a minute when docked.
# The H50 (ov43gb) has its status at 2/2, which vacuum-map-parser-xiaomi does not map yet.
NO_STATE = {"viomi.vacuum.x1", "xiaomi.vacuum.ov43gb"}
REPORTS_STATE = [v for v in VENDORS if v.model not in NO_STATE]
CLOUD_STATE = [v for v in REPORTS_STATE if v.api in ("VIOMI", "ROIDMI", "DREAME")]


async def _settle_docked(vacuum: AsyncVacuum, freezer: FrozenDateTimeFactory) -> None:
    # Long enough for a vendor that cannot report its state to be down to a check per minute, and to
    # end 40 s after that check: a clean starting now is the worst case for the minute check.
    await vacuum.cycles(freezer, 2 * CYCLES_PER_MINUTE)


@pytest.mark.parametrize("vendor", REPORTS_STATE, ids=str)
async def test_cleaning_start_is_followed_at_the_next_update(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """A docked Viomi was checked once a minute, so the map started following a clean up to 60 s late."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    await _settle_docked(vacuum, freezer)

    vacuum.home.start_cleaning()
    vacuum.home.map_version += 1
    before = vacuum.home.downloads
    shown = hass.states.get(IMAGE).state
    await vacuum.cycles(freezer, 1)

    assert vacuum.home.downloads - before == 1
    assert hass.states.get(IMAGE).state != shown


@pytest.mark.parametrize("vendor", CLOUD_STATE, ids=str)
async def test_cloud_state_works_without_the_lan(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """The state is read through the Xiaomi cloud, so a vacuum on another network is followed too."""
    vacuum = setup_vacuum(vendor)
    vacuum.home.lan.reachable = False
    await vacuum.setup()
    await _settle_docked(vacuum, freezer)
    before = vacuum.home.downloads
    await vacuum.cycles(freezer, DOCKED_MINUTES * CYCLES_PER_MINUTE)
    assert vacuum.home.downloads == before, "a vacuum the cloud reports docked needs no map check"

    vacuum.home.start_cleaning()
    await vacuum.cycles(freezer, 3)

    assert vacuum.home.downloads - before == 3
    assert _lan_issue(hass, vacuum.entry.entry_id) is None


@pytest.mark.parametrize("vendor", CLOUD_STATE, ids=str)
async def test_cloud_state_is_read_with_the_python_miio_property(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor
) -> None:
    """One small cloud RPC per update: miIO `get_prop` for Viomi, MIoT `get_properties` for the others."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    vacuum.home.lan.cloud_calls.clear()
    await vacuum.cycles(freezer, 1)

    (state_key,) = vendor.docked
    if isinstance(state_key, str):
        expected = {"method": "get_prop", "params": [state_key]}
    else:
        siid, piid = state_key
        expected = {"method": "get_properties", "params": [{"did": DEVICE_ID, "siid": siid, "piid": piid}]}
    assert vacuum.home.lan.cloud_calls == [expected]


@pytest.mark.parametrize("vendor", CLOUD_STATE, ids=str)
@pytest.mark.parametrize(
    "fault",
    ["offline", "unexpected", "session expired", "bad gateway"],
)
async def test_unreadable_cloud_state_falls_back_to_the_map_check(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor, fault: str
) -> None:
    """When the cloud cannot tell the state, the map is checked once a minute as before: never a failed
    update and never a map that stops following."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    rpc = f"/v2/home/rpc/{DEVICE_ID}"
    if fault == "offline":
        vacuum.home.lan.online = False
    elif fault == "unexpected":
        vacuum.home.lan.properties = {key: "busy" for key in vendor.docked}
    elif fault == "bad gateway":
        vacuum.home.cloud.on("POST", f"{API}{rpc}", bad_gateway)
    else:
        # Only the state read is refused; the download that follows still works.
        vacuum.home.cloud.on("POST", f"{API}{rpc}", lambda _r: FakeResponse(
            status=401, body='{"code":3,"message":"auth err"}'))

    await _settle_docked(vacuum, freezer)
    before = vacuum.home.downloads
    await vacuum.cycles(freezer, DOCKED_MINUTES * CYCLES_PER_MINUTE)
    assert 0 < vacuum.home.downloads - before <= DOCKED_MINUTES

    vacuum.home.map_version += 1
    shown = hass.states.get(IMAGE).state
    await vacuum.cycles(freezer, CYCLES_PER_MINUTE)
    assert hass.states.get(IMAGE).state != shown


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


# --- secrets ---------------------------------------------------------------------------------------
# Every test in this file ends with the secret scan of `setup_vacuum`; these drive the failure paths,
# whose messages quote what the cloud or aiohttp said.

MAP_URL_ENDPOINTS = ("/v2/home/get_interim_file_url", "/v2/home/get_interim_file_url_pro", "/home/getmapfileurl")


def _api_times_out(home: VacuumHome) -> None:
    for endpoint in MAP_URL_ENDPOINTS:
        home.cloud.on("POST", f"{API}{endpoint}", connection_timeout)


def _storage_echoes_the_signature(home: VacuumHome) -> None:
    home.cloud.on("GET", MAP_URL, lambda r: FakeResponse(status=403, body=f"SignatureDoesNotMatch {r.url}"))


def _storage_times_out(home: VacuumHome) -> None:
    home.cloud.on("GET", MAP_URL, connection_timeout)


def _vacuum_off_the_lan(home: VacuumHome) -> None:
    home.lan.reachable = False


FAILURES = {
    "cloud API timeout": _api_times_out,
    "map refused": _storage_echoes_the_signature,
    "map download timeout": _storage_times_out,
    "vacuum off the LAN": _vacuum_off_the_lan,
}


@pytest.mark.parametrize("failure", FAILURES)
@pytest.mark.parametrize("vendor", VENDORS, ids=str)
async def test_failed_updates_show_no_secrets(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory, vendor: Vendor, failure: str
) -> None:
    """A failed update's message reaches the UI and the log; neither may quote a secret (upstream #778)."""
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    FAILURES[failure](vacuum.home)
    vacuum.home.start_cleaning()  # every cycle downloads
    vacuum.home.map_version += 1

    with patch.object(Backoff, "sleep", AsyncMock()):  # Roborock retries the map name off the LAN
        await vacuum.cycles(freezer, 3, failing=None)
        with lan_vacuum(vacuum.home.lan):
            await vacuum.entry.runtime_data.coordinator.force_update_data()
            await hass.async_block_till_done()

    if failure == "cloud API timeout":
        assert vacuum.failure_messages, "the timeout never reached the coordinator"


@pytest.mark.parametrize("vendor", [*VENDORS, UNSUPPORTED], ids=_ids([*VENDORS, UNSUPPORTED]))
async def test_diagnostics_show_no_secrets(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, setup_vacuum, freezer: FrozenDateTimeFactory,
    vendor: Vendor,
) -> None:
    """The diagnostics download after real updates, with what each vendor really reports."""
    assert await async_setup_component(hass, "diagnostics", {})
    vacuum = setup_vacuum(vendor)
    await vacuum.setup()
    await vacuum.cycles(freezer, 2)

    diagnostics = json.dumps(await get_diagnostics_for_config_entry(hass, hass_client, vacuum.entry))

    found = leaked([diagnostics], {**vendor.secrets, "map URL": MAP_URL, "host": HOST, "mac": MAC})
    assert found == set()
    assert vacuum.entry.data["model"] in diagnostics, "diagnostics still say which vacuum it is"


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
    assert issue.severity is ir.IssueSeverity.WARNING, "a vacuum on another network on purpose is not an error"
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


async def test_vacuum_on_another_network_keeps_its_position_current(
    hass: HomeAssistant, setup_vacuum, freezer: FrozenDateTimeFactory
) -> None:
    """The LAN does not reach the vacuum, so the current map and the position come through the cloud.

    The cloud map object does not change while the robot drives (upstream #720), so its hash alone
    called every download "unchanged" and the image froze after UNCHANGED_MAP_DOWNLOADS.
    """
    vendor = next(v for v in VENDORS if v.model == "xiaomi.vacuum.ov81gl")
    vacuum = setup_vacuum(vendor, ["charger", "path", "vacuum_position"])
    vacuum.home.lan.reachable = False  # the cloud still forwards to the vacuum (FakeLanVacuum.cloud_rpc)
    await vacuum.setup()
    vacuum.home.start_cleaning()
    lan = vacuum.home.lan
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)

    for step in range(8):
        lan.properties[(10, 4)] = json.dumps({"position": [-150 + 50 * step, 0, 0]})
        await vacuum.cycles(freezer, 1)

    assert any(call["method"] == "get_properties" for call in lan.cloud_calls)
    assert lan.calls_on_event_loop == []
    assert [e.data["entity_id"] for e in changes].count(IMAGE) == 8


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
