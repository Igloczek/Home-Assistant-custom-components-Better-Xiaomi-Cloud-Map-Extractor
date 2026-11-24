"""The vacuum on the local network, behind python-miio's UDP protocol.

python-miio talks to the vacuum with blocking UDP round trips (`MiIOProtocol.send`, 2 s timeout and
retries). The fake replaces exactly that call, so the real `MiotDevice`, `RoborockVacuum` and
`DreameVacuum` code runs above it, and it blocks the calling thread with `time.sleep` like a real
round trip does. Home Assistant's blocking-call detection (enabled in every test) raises on
`time.sleep` inside the event loop, so a python-miio call made on the event loop fails the update
the way it shows up as "Detected blocking call" on a real installation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from miio.exceptions import DeviceException
from miio.miioprotocol import MiIOProtocol

# Long enough to block, short enough to keep the suite fast.
ROUND_TRIP = 0.001


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@dataclass
class LanCall:
    command: str
    parameters: Any
    on_event_loop: bool


@dataclass
class FakeLanVacuum:
    model: str
    # MIoT properties by (siid, piid); a missing one answers code -4003 like the real vacuums.
    properties: dict[tuple[int, int], Any] = field(default_factory=dict)
    # Roborock `get_status` state: 8 is "Charging" (docked), 5 is "Cleaning".
    roborock_state: int = 8
    roborock_map_name: str = "map-name"
    reachable: bool = True
    calls: list[LanCall] = field(default_factory=list)

    def send(self, command: str, parameters: Any = None) -> Any:
        self.calls.append(LanCall(command, parameters, _on_event_loop()))
        time.sleep(ROUND_TRIP)  # the UDP round trip blocks the calling thread
        if not self.reachable:
            raise DeviceException("Unable to discover the device 192.168.1.50")
        if command == "miIO.info":  # python-miio asks once for the model it was not given
            return {"model": self.model, "fw_ver": "3.5.8_0000", "hw_ver": "Linux", "mac": "AA:BB:CC:DD:EE:FF",
                    "token": "0" * 32, "ap": {"ssid": "home", "rssi": -50}, "netif": {"localIp": "192.168.1.50"}}
        if command == "get_properties":
            return [self._property(p["siid"], p["piid"]) for p in parameters]
        if command == "action":
            return {"code": 0}
        if command == "get_status":
            return [{"state": self.roborock_state}]
        if command == "get_map_v1":
            return [self.roborock_map_name]
        raise AssertionError(f"Unexpected miIO command {command}")

    def _property(self, siid: int, piid: int) -> dict[str, Any]:
        answer: dict[str, Any] = {"did": f"{siid}-{piid}", "siid": siid, "piid": piid}
        if (siid, piid) not in self.properties:
            return {**answer, "code": -4003}
        return {**answer, "code": 0, "value": self.properties[(siid, piid)]}

    @property
    def calls_on_event_loop(self) -> list[str]:
        return [call.command for call in self.calls if call.on_event_loop]


@contextmanager
def lan_vacuum(vacuum: FakeLanVacuum) -> Iterator[FakeLanVacuum]:
    def send(_protocol, command, parameters=None, retry_count=3, *, extra_parameters=None):
        return vacuum.send(command, parameters)

    with patch.object(MiIOProtocol, "send", send):
        yield vacuum
