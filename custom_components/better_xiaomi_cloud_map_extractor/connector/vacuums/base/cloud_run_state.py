"""The run state of vacuums whose status is read through the Xiaomi cloud.

Viomi, Roidmi and Dreame have no status in their map, and this integration does not talk to them on
the local network for it. The Xiaomi cloud forwards a miIO command to the vacuum
(`/v2/home/rpc/<did>`, as the Mi Home app does), so one small request per update tells whether the
vacuum moves, wherever Home Assistant runs. The properties and their values are python-miio's.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Self

from ...utils.dict_operations import path_extractor
from ...xiaomi_cloud.connector import XiaomiCloudConnector

_LOGGER = logging.getLogger(__name__)
# Downloads after the vacuum stops, so the map shows where it ended (as the LAN vendors do).
OFF_UPDATES = 3


@dataclass(frozen=True)
class CloudRunStateProperty:
    # The run state values of a vacuum that does not move.
    idle_at: frozenset[int]
    # A miIO property, read with `get_prop` (Viomi)...
    miio_name: str | None = None
    # ...or a MIoT property, read with `get_properties`.
    siid: int | None = None
    piid: int | None = None

    def request(self: Self, device_id: str) -> dict[str, Any]:
        if self.miio_name is not None:
            return {"method": "get_prop", "params": [self.miio_name]}
        return {"method": "get_properties", "params": [{"did": device_id, "siid": self.siid, "piid": self.piid}]}

    def value(self: Self, response: Any) -> Any:
        result = path_extractor(response, "result")
        if not isinstance(result, list) or len(result) != 1:
            return None
        if self.miio_name is not None:
            return result[0]
        item = result[0]
        return item.get("value") if isinstance(item, dict) and item.get("code") == 0 else None


def _models(models: list[str], prop: CloudRunStateProperty) -> dict[str, CloudRunStateProperty]:
    return dict.fromkeys(models, prop)


# python-miio ViomiVacuum (miio/integrations/vacuum/viomi/viomivacuum.py): SUPPORTED_MODELS, and
# `status()` reads "run_state" with get_prop. ViomiVacuumState: 0 IdleNotDocked, 1 Idle, 2 Idle2,
# 3 Cleaning, 4 Returning, 5 Docked, 6 VacuumingAndMopping.
VIOMI_RUN_STATES = _models(
    ["viomi.vacuum.v6", "viomi.vacuum.v7", "viomi.vacuum.v8", "viomi.vacuum.v10", "viomi.vacuum.v13"],
    CloudRunStateProperty(idle_at=frozenset({0, 1, 2, 5}), miio_name="run_state"),
)

# python-miio RoidmiVacuumMiot (miio/integrations/vacuum/roidmi/roidmivacuum_miot.py): "state" is
# siid 2 piid 1 on roidmi.vacuum.v60. RoidmiState: 1 Dormant, 2 Idle, 3 Paused, 4 Sweeping,
# 5 GoCharging, 6 Charging, 7 Error, 8 Rfctrl (remote control), 9 Fullcharge, 10 Shutdown,
# 11 FindChargerPause.
ROIDMI_RUN_STATES = _models(
    ["roidmi.vacuum.v60"],
    CloudRunStateProperty(idle_at=frozenset({1, 2, 3, 6, 7, 9, 10, 11}), siid=2, piid=1),
)

# python-miio DreameVacuum (miio/integrations/vacuum/dreame/dreamevacuum_miot.py): "device_status"
# is siid 3 piid 2 on the 1C (mc1808) and siid 2 piid 1 on the models with the F9 mapping.
# DeviceStatus: 1 Sweeping, 2 Idle, 3 Paused, 4 Error, 5 GoCharging, 6 Charging, 7 Mopping,
# 13 ManualSweeping.
_DREAME_IDLE = frozenset({2, 3, 4, 6})
DREAME_RUN_STATES = {
    **_models(["dreame.vacuum.mc1808"], CloudRunStateProperty(idle_at=_DREAME_IDLE, siid=3, piid=2)),
    **_models(
        ["dreame.vacuum.p2008", "dreame.vacuum.p2009", "dreame.vacuum.p2028", "dreame.vacuum.p2041o",
         "dreame.vacuum.p2150a", "dreame.vacuum.p2150o"],
        CloudRunStateProperty(idle_at=_DREAME_IDLE, siid=2, piid=1),
    ),
}


class CloudRunState:
    """Whether the vacuum moves, from its run state read through the cloud."""

    def __init__(
            self: Self, connector: XiaomiCloudConnector, server: str, device_id: str,
            prop: CloudRunStateProperty | None) -> None:
        self._connector = connector
        self._server = server
        self._device_id = str(device_id)
        self._prop = prop
        self._off_counter = 0

    async def should_update_map(self: Self) -> bool | None:
        """True while the vacuum moves, False once it stopped, None when the cloud cannot tell."""
        if self._prop is None:
            return None
        value = await self._read()
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        if value in self._prop.idle_at:
            self._off_counter += 1
            _LOGGER.debug("Run state from the cloud: %d (not moving). Off counter: %d", value, self._off_counter)
            return self._off_counter <= OFF_UPDATES
        if self._off_counter:
            _LOGGER.debug("Run state from the cloud: %d (moving), updating the map every time", value)
        self._off_counter = 0
        return True

    async def _read(self: Self) -> Any:
        url = self._connector.get_api_url(self._server) + "/v2/home/rpc/" + self._device_id
        params = {"data": json.dumps(self._prop.request(self._device_id), separators=(",", ":"))}
        try:
            response = await self._connector.execute_api_call_encrypted(url, params)
        except Exception as ex:
            # The map download that may follow handles the session and reports failures.
            _LOGGER.debug("Could not read the run state from the cloud: %r", ex)
            return None
        value = self._prop.value(response)
        if not isinstance(value, int) or isinstance(value, bool):
            _LOGGER.debug("The cloud did not report a run state (code %s)",
                          response.get("code") if isinstance(response, dict) else None)
        return value
