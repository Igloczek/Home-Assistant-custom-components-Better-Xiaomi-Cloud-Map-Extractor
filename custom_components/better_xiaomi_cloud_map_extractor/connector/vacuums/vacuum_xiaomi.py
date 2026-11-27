import asyncio
import base64
import contextlib
import json
import logging
import zlib
from typing import Self, Any

from miio.exceptions import DeviceException
from miio.miot_device import MiotDevice
from vacuum_map_parser_base.map_data import MapData
from vacuum_map_parser_xiaomi.aes_decryptor import gen_md5_key
from vacuum_map_parser_xiaomi.map_data_parser import XiaomiMapDataParser

from .base.model import VacuumConfig, VacuumApi
from .base.vacuum_v2 import BaseXiaomiCloudVacuumV2
from .xiaomi_miot import (
    Property,
    as_int,
    decode_trajectory,
    layout_for,
    object_name,
    object_path,
    room_key_rule, ROOM_KEY_LOG,
    vacuum_position,
    with_live_data,
    with_unique_room_ids,
)
from ..utils.dict_operations import path_extractor
from ..utils.exceptions import (
    FailedConnectionException,
    FailedMapParseException,
    XiaomiCloudMapExtractorException,
)

_LOGGER = logging.getLogger(__name__)
OFF_UPDATES = 3
# The Xiaomi Home map plugin keys the map with the last 16 characters of the model
# (`Device.model.slice(-16)`): "xiaomi.vacuum.ov81gl" -> "mi.vacuum.ov81gl",
# "xiaomi.vacuum.c107" -> "aomi.vacuum.c107". AES-128 needs exactly 16 bytes.
MODEL_KEY_LENGTH = 16


class XiaomiCloudVacuum(BaseXiaomiCloudVacuumV2):
    def __init__(self, vacuum_config: VacuumConfig) -> None:
        super().__init__(vacuum_config)
        self._token = vacuum_config.token
        self._host = vacuum_config.host

        self._miot_device = MiotDevice(self._host, self._token, timeout=2, mapping={})

        self._xiaomi_map_data_parser = XiaomiMapDataParser(
            vacuum_config.palette,
            vacuum_config.sizes,
            vacuum_config.drawables,
            vacuum_config.image_config,
            vacuum_config.texts
        )

        # Where this model keeps its status, current map and live position (xiaomi_miot.py).
        self._layout = layout_for(self.model)
        self._off_counter = 0
        self._state: dict[Property, Any] | None = None
        # The object paths the vacuum reported, by object name (upstream #762).
        self._object_paths: dict[str, str] = {}
        # The current map properties of the last answer, for when neither the LAN nor the cloud answers.
        self._last_map_state: dict[Property, Any] = {}
        self._live_path: list[dict[str, Any]] = []
        self._live_position: dict[str, Any] | None = None
        self._logged_room_key_rules: set[str] = set()

    @property
    def _model_key(self: Self) -> str:
        return self.model[-MODEL_KEY_LENGTH:]

    async def should_update_map(self: Self) -> bool | None:
        try:
            values = await self._read_lan([self._layout.status])
        except DeviceException as de:
            if "token" in repr(de):
                raise FailedConnectionException(de)
            # Busy cleaning or on another network: unknown, so the map keeps following the cloud
            # (it used to stop downloading for good, upstream PR #748).
            _LOGGER.debug("The vacuum did not report its status on the local network (%s)", type(de).__name__)
            return None

        status_value = as_int(values.get(self._layout.status))
        if status_value is None:
            return None  # not a status: a model this integration does not know
        if status_value in self._layout.idle_at:
            self._off_counter += 1
            _LOGGER.debug(
                "Vacuum is not moving. Off counter: %d", self._off_counter)
            return self._off_counter <= OFF_UPDATES
        self._off_counter = 0
        return True

    @staticmethod
    def vacuum_platform() -> VacuumApi:
        return VacuumApi.XIAOMI

    @property
    def map_archive_extension(self) -> str:
        return "zlib.enc"

    @property
    def map_data_parser(self) -> XiaomiMapDataParser:
        return self._xiaomi_map_data_parser

    async def get_map(self: Self) -> tuple[MapData, bytes]:
        self._state = await self._read_state()
        layout = self._layout
        self._object_paths = {
            path.split("/")[-1]: path
            for prop in (layout.map_obj_name, layout.trajectory_obj_name)
            if prop and (path := object_path(self._state.get(prop))) is not None
        }
        self._live_path, self._live_position = await self._read_live_data(self._state)
        return await super().get_map()

    async def get_map_name(self: Self) -> str:
        state = self._state if self._state is not None else await self._read_state()
        layout = self._layout
        name = object_name(state.get(layout.map_obj_name)) if layout.map_obj_name else None
        if name is None and layout.current_map_id:
            # Upstream #720: map "0" may be a deleted map on a vacuum that had several.
            current = as_int(state.get(layout.current_map_id))
            name = str(current) if current is not None else None
        if name is None:
            return await super().get_map_name()
        return name

    async def _read_state(self: Self) -> dict[Property, Any]:
        """The status, current map and live data properties: the LAN first, then the cloud."""
        layout = self._layout
        properties = [p for p in (layout.status, layout.map_obj_name, layout.current_map_id,
                                  layout.trajectory_obj_name, layout.vacuum_position) if p]
        map_properties = [p for p in (layout.map_obj_name, layout.current_map_id) if p]
        try:
            values = await self._read_lan(properties)
        except DeviceException as lan_error:
            if not map_properties:
                return {}
            _LOGGER.debug("The vacuum did not answer on the local network (%s), asking the cloud",
                          type(lan_error).__name__)
            try:
                values = await self._read_cloud(properties)
            except XiaomiCloudMapExtractorException:
                raise  # an expired session is renewed by the extractor
            except Exception as cloud_error:
                if not self._last_map_state:
                    raise lan_error from cloud_error
                values = {}
            if all(values.get(p) is None for p in map_properties):
                if not self._last_map_state:
                    # Never told which map is current: report the vacuum unreachable rather than show
                    # map "0", which may be a deleted one (upstream #720).
                    raise
                # The map the vacuum last reported; the vacuum_unreachable repair says why.
                return {**values, **self._last_map_state}
        if any(values.get(p) is not None for p in map_properties):
            self._last_map_state = {p: values.get(p) for p in map_properties}
        return values

    async def _read_lan(self: Self, properties: list[Property]) -> dict[Property, Any]:
        """Properties from the vacuum on the LAN; `lan_reachable` says whether it answered (repairs.py)."""
        try:
            response = await asyncio.to_thread(
                self._miot_device.send,
                "get_properties",
                [{"did": f"{siid}-{piid}", "siid": siid, "piid": piid} for siid, piid in properties],
            )
        except DeviceException as de:
            if "token" not in repr(de):
                self.lan_reachable = False
            raise
        self.lan_reachable = True
        return self._property_values(response)

    async def _read_cloud(self: Self, properties: list[Property]) -> dict[Property, Any]:
        response = await self._connector.get_other_info(
            str(self._device_id), "get_properties",
            parameters=[{"did": str(self._device_id), "siid": siid, "piid": piid} for siid, piid in properties])
        return self._property_values(path_extractor(response, "result"))

    @staticmethod
    def _property_values(response: Any) -> dict[Property, Any]:
        if not isinstance(response, list):
            return {}
        return {
            (item.get("siid"), item.get("piid")): item.get("value")
            for item in response
            if isinstance(item, dict) and item.get("code", 0) == 0
        }

    async def _read_live_data(
        self: Self, state: dict[Property, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """The robot position and path of the current clean (upstream PR #781, every model that has them)."""
        layout = self._layout
        if as_int(state.get(layout.status)) in layout.docked_at:
            return [], None  # the trajectory property still names the previous clean's path
        position = vacuum_position(state.get(layout.vacuum_position)) if layout.vacuum_position else None
        name = object_name(state.get(layout.trajectory_obj_name)) if layout.trajectory_obj_name else None
        if name is None:
            return [], position
        try:
            raw = await self.get_raw_map_data(name)
            path = await asyncio.to_thread(decode_trajectory, raw, self._map_key()) if raw else []
        except Exception as e:  # live data is optional, never fail the map for it
            _LOGGER.debug("Failed to read the trajectory of the current clean (%s)", type(e).__name__)
            path = []
        return path, position

    def _map_key(self: Self) -> bytes | None:
        try:
            return bytes.fromhex(gen_md5_key(self._model_key, str(self._device_id)))
        except ValueError:
            return None

    async def get_map_url(self, map_name: str) -> str | None:
        return await self.get_fallback_map_url(map_name)

    async def get_raw_map_data(self: Self, map_name: str | None) -> bytes | None:
        """The object at the path the vacuum reported, then at the account's path as before.

        The vacuum may store its objects under another uid than the account's (upstream #762): the
        cloud signs a URL for the account's path all the same, and the download answers 404.
        """
        path = self._object_paths.get(map_name) if map_name is not None else None
        if path is not None and path != self._account_path(map_name):
            url = await self.get_fallback_map_url(map_name, obj_name=path)
            raw = await self._connector.get_raw_map_data(url) if url is not None else None
            if raw is not None:
                return raw
            _LOGGER.debug("Nothing at the object path the vacuum reported, trying the account's")
        return await super().get_raw_map_data(map_name)

    def _account_path(self: Self, name: str) -> str:
        return f"{self._user_id}/{self._device_id}/{name}"

    def decode_and_parse(self, raw_map: bytes) -> MapData:
        # Encrypted maps come in a {"version": 2, "data": ...} envelope or as raw ciphertext; c107
        # uploads base64(zlib(JSON)) without one, decoded when decryption fails.
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, ValueError):
            envelope = json.loads(raw_map)
            if isinstance(envelope, dict) and isinstance(envelope.get("data"), str):
                raw_map = base64.decodebytes(envelope["data"].encode("latin1"))

        try:
            try:
                decoded_map = self.map_data_parser.unpack_map(
                    raw_map.hex(),
                    model=self._model_key,
                    device_id=str(self._device_id),
                )
                payload = json.loads(decoded_map) if isinstance(decoded_map, str) else decoded_map
            except Exception:
                payload = _decode_unencrypted_map(raw_map)
                if payload is None:
                    raise
            if isinstance(payload, dict):
                rule = room_key_rule(payload, self.model)
                # Once per rule and vacuum, counted only when the line is really written.
                if rule and rule not in self._logged_room_key_rules and _LOGGER.isEnabledFor(logging.DEBUG):
                    self._logged_room_key_rules.add(rule)
                    _LOGGER.debug(ROOM_KEY_LOG[rule], self.model)
                payload = with_unique_room_ids(payload, self.model)
                payload = with_live_data(payload, self._live_path, self._live_position)
            return self.map_data_parser.parse(payload)
        except Exception as err:
            # A map in another vendor's format fails here with whatever the cipher, zlib or JSON
            # step raises; report it as a parse failure so the coordinator can offer the right format.
            _LOGGER.debug("Failed to read the %s map: %r", self.model, err)
            raise FailedMapParseException(f"{self.model}: the map could not be read in the Xiaomi format") from err

    def additional_data(self: Self) -> dict[str, Any]:
        super_data = super().additional_data()
        try:
            enc_key = gen_md5_key(self._model_key, str(self._device_id))
        except ValueError:
            # The model is shorter than an AES key, so it is not a Xiaomi-format vacuum.
            enc_key = None

        return {**super_data, "enc_key": enc_key}


def _decode_unencrypted_map(raw_map: bytes) -> dict[str, Any] | None:
    """Decode an unencrypted `base64(zlib(map JSON))` or `zlib(map JSON)` Xiaomi map payload.

    Some models (such as `xiaomi.vacuum.c107`, Mijia M30 Pro) upload compressed map JSON as plain
    base64 without AES encryption or the `{"version": 2, "data": ...}` envelope.
    """
    candidates = [raw_map]
    compact = b"".join(raw_map.split())
    if compact:
        with contextlib.suppress(ValueError):
            candidates.insert(0, base64.b64decode(compact, validate=True))
    for candidate in candidates:
        try:
            payload = json.loads(zlib.decompress(candidate))
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None
