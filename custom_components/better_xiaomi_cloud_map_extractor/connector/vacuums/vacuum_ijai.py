import asyncio
import base64
import binascii
import json
import logging
import re
import zlib
from typing import Self, Any

from Crypto.Hash import MD5
from miio.miot_device import MiotDevice
from miio.exceptions import DeviceException
from vacuum_map_parser_base.map_data import MapData
from vacuum_map_parser_ijai.map_data_parser import IjaiMapDataParser
from vacuum_map_parser_ijai.status_mapping import get_status_mapping
from vacuum_map_parser_ijai.aes_decryptor import aes_decrypt, aes_encrypt
from vacuum_map_parser_ijai.RobotMap_pb2 import RobotMap

from .base.vacuum_v2 import BaseXiaomiCloudVacuumV2
from .base.model import VacuumConfig, VacuumApi
from ..utils.dict_operations import path_extractor
from ..utils.exceptions import FailedConnectionException, FailedMapParseException

_LOGGER = logging.getLogger(__name__)
OFF_UPDATES = 3

# The map key is md5(wifi_sn + owner uid + "device id"). The "device id" is usually the cloud
# device id, but ijai.vacuum.v3, v17 and v18 also encrypt maps with a small index ("0", "2"),
# and the same vacuum switches between them from one map to the next. These are tried after
# the cloud device id.
MAP_KEY_FALLBACK_DEVICE_IDS = [str(i) for i in range(10)]
# The key is either the MD5 digest as 16 bytes ("hex") or its middle 16 hex digits. v3.0.0-alpha-24
# (vacuum-map-parser-ijai 0.1.0) used the digest for every model; 0.1.1 uses it only for the
# Mi Home plugin's list. The digest goes first, so a vacuum that decrypted before still does.
MAP_KEY_TYPES_HEX = [True, False]
DECRYPT_ERRORS = (ValueError, zlib.error)

# Properties that may hold the WiFi serial number used in the map key: siid 1 piid 3 on
# 2019-2021 vacuums, siid 1 piid 5 on newer ones, and the multi-value siid 7 piid 45 on all.
WIFI_SN_PROPERTIES = [(1, 3), (1, 5), (7, 45)]
WIFI_SN_IN_MULTI_PROPERTY = re.compile(r"[0-9A-Z/]*[A-Z][0-9A-Z/]*")
# v3.0.0-alpha-24 accepted only these lengths; 19 (ijai.vacuum.v1) is tried after its rules.
ALPHA_24_WIFI_SN_LEN = [18, 20]

# Models that only upload a fresh map when asked to, like the Mi Home app does: action
# "upload-by-maptype" (siid 10, aiid 6) with map type 0 (real-time map). Without it the cloud keeps
# the map uploaded at the end of the previous cleaning.
REALTIME_UPLOAD_ACTION: dict[str, tuple[int, int]] = {
    "xiaomi.vacuum.b106eu": (10, 6),
}


class IjaiCloudVacuum(BaseXiaomiCloudVacuumV2):
    WIFI_INFO_SN_POSSIBLE_LEN = [18, 19, 20]

    def __init__(self, vacuum_config: VacuumConfig):
        super().__init__(vacuum_config)
        self._token = vacuum_config.token
        self._host = vacuum_config.host
        self._mac = vacuum_config.device_info.mac
        self._wifi_info_sn = None
        # (owner id, device id, hex key) the last map was encrypted with; tried first next time.
        self._map_key_ids: tuple[str, str, bool] | None = None

        self._miot_device = MiotDevice(self._host, self._token, timeout=2)

        self._ijai_map_data_parser = IjaiMapDataParser(
            vacuum_config.palette,
            vacuum_config.sizes,
            vacuum_config.drawables,
            vacuum_config.image_config,
            vacuum_config.texts
        )

        self._status_mapping = get_status_mapping(self.model)
        self._off_counter = 0

    @property
    def should_update_map(self: Self) -> bool:
        try:
            status_value = self._miot_device.get_property_by(self._status_mapping.siid,
                                                             self._status_mapping.piid)[0]["value"]

            if status_value in self._status_mapping.idle_at:
                self._off_counter += 1
                _LOGGER.debug(
                    "Vacuum is not moving. Off counter: %d", self._off_counter)
                return self._off_counter <= OFF_UPDATES
            else:
                self._off_counter = 0
                return True
        except DeviceException as de:
            if "token" not in repr(de):
                return False
            raise FailedConnectionException(de)

    @staticmethod
    def vacuum_platform() -> VacuumApi:
        return VacuumApi.IJAI

    @property
    def map_archive_extension(self) -> str:
        return "zlib.enc"

    @property
    def map_data_parser(self) -> IjaiMapDataParser:
        return self._ijai_map_data_parser

    async def get_map_url(self, map_name: str) -> str | None:
        action = REALTIME_UPLOAD_ACTION.get(self.model)
        if action is not None:
            await self._request_realtime_upload(*action)
        # The "pro" endpoint first: v3.0.0-alpha-24 asked only that one, for every model. Then the
        # standard one: ijai.vacuum.v1 and v3 answer "pro" with code -8 ("get none
        # fileStorageConfig of the model and bucket").
        url = await self.get_fallback_map_url(map_name)
        if url is not None:
            return url
        return await super().get_map_url(map_name)

    async def _request_realtime_upload(self, siid: int, aiid: int) -> None:
        # The vacuum uploads asynchronously, so the map fetched right after may still be the
        # previous one. Sent over the LAN this action times out ("user ack timeout").
        params = {"did": str(self._device_id), "siid": siid, "aiid": aiid, "in": [0]}
        try:
            await self._connector.execute_api_call_encrypted(
                self._connector.get_api_url(self._server) + "/v2/miotspec/action",
                {"data": json.dumps({"params": params})})
        except Exception as ex:  # the last uploaded map is still usable
            _LOGGER.debug("Failed to request a real-time map upload: %r", ex)

    async def get_raw_map_data(self, map_name: str | None) -> bytes | None:
        raw_map = await super().get_raw_map_data(map_name)
        if raw_map is not None and not self._wifi_info_sn:
            self._wifi_info_sn = await self._resolve_wifi_info_sn()
        return raw_map

    async def _resolve_wifi_info_sn(self) -> str:
        # Ask the vacuum on the local network first; when it cannot be reached (another network,
        # VLAN isolation, no host configured), read the same properties through the cloud.
        try:
            wifi_info_sn = await asyncio.get_running_loop().run_in_executor(None, self.get_wifi_info_sn)
            if wifi_info_sn:
                return wifi_info_sn
            _LOGGER.debug("The vacuum did not report a WiFi serial number locally, asking the cloud")
        except Exception as ex:
            _LOGGER.debug("Reading the WiFi serial number locally failed (%r), asking the cloud", ex)
        try:
            wifi_info_sn = await self._get_wifi_info_sn_from_cloud()
        except Exception as ex:
            raise FailedConnectionException(ex) from ex
        if not wifi_info_sn:
            _LOGGER.error("Failed to get wifi_sn from vacuum")
            raise FailedConnectionException(Exception("The vacuum did not report its WiFi serial number"))
        return wifi_info_sn

    async def _get_wifi_info_sn_from_cloud(self) -> str | None:
        response = await self._connector.get_other_info(
            str(self._device_id), "get_properties",
            parameters=[{"did": str(self._device_id), "siid": siid, "piid": piid}
                        for siid, piid in WIFI_SN_PROPERTIES])
        values = {
            (item.get("siid"), item.get("piid")): item.get("value")
            for item in path_extractor(response, "result") or []
            if isinstance(item, dict)
        }
        return self._wifi_info_sn_from_properties(values)

    def get_wifi_info_sn(self) -> str | None:
        # Read the properties one by one and stop at the first v3.0.0-alpha-24 match, as it did.
        values = {}
        for siid, piid in WIFI_SN_PROPERTIES:
            data = self._miot_device.get_property_by(siid, piid)
            values[(siid, piid)] = data[0].get("value") if data and isinstance(data[0], dict) else None
            wifi_info_sn = self._alpha_24_wifi_info_sn(values)
            if wifi_info_sn:
                return wifi_info_sn
        return self._wifi_info_sn_from_properties(values)

    def _alpha_24_wifi_info_sn(self, values: dict[tuple[int, int], Any]) -> str | None:
        # The serial v3.0.0-alpha-24 picked, so a vacuum that worked keeps the same map key.
        for prop in [(1, 3), (1, 5)]:
            value = values.get(prop)
            if isinstance(value, str) and len(value) in ALPHA_24_WIFI_SN_LEN and value.isupper():
                return value
        multi_value = values.get((7, 45))
        if not isinstance(multi_value, str):
            return None
        wifi_info_sn = None
        for prop in multi_value.split(','):
            cleaned_prop = prop.replace('"', '')
            if str(self._user_id) in cleaned_prop:
                cleaned_prop = cleaned_prop.split(';')[0]
            if (
                    len(cleaned_prop) in ALPHA_24_WIFI_SN_LEN
                    and cleaned_prop.isalnum()
                    and cleaned_prop.isupper()):
                wifi_info_sn = cleaned_prop
        return wifi_info_sn

    def _wifi_info_sn_from_properties(self, values: dict[tuple[int, int], Any]) -> str | None:
        wifi_info_sn = self._alpha_24_wifi_info_sn(values)
        if wifi_info_sn:
            return wifi_info_sn
        for prop in [(1, 3), (1, 5)]:
            value = values.get(prop)
            if (
                    isinstance(value, str)
                    and len(value) in self.WIFI_INFO_SN_POSSIBLE_LEN
                    and value.isupper()):
                return value

        multi_value = values.get((7, 45))
        if not isinstance(multi_value, str):
            return None
        wifi_info_sn = None
        for prop in multi_value.split(','):
            cleaned_prop = prop.replace('"', '').strip("[] ")
            owner_entry = str(self._user_id) in cleaned_prop
            if owner_entry:
                # "<serial>;<owner id>...": some serials contain a slash (S20, S10).
                cleaned_prop = cleaned_prop.split(';')[0]
            if (
                    len(cleaned_prop) in self.WIFI_INFO_SN_POSSIBLE_LEN
                    and WIFI_SN_IN_MULTI_PROPERTY.fullmatch(cleaned_prop)):
                if owner_entry:
                    return cleaned_prop
                wifi_info_sn = cleaned_prop
        return wifi_info_sn

    def _logged_in_user_id(self) -> str | None:
        try:
            user_id = self._connector.to_config().user_id
        except Exception:
            return None
        return str(user_id) if user_id else None

    def _map_key_id_candidates(self) -> list[tuple[str, str, bool]]:
        # The first candidate is the key v3.0.0-alpha-24 used. On a vacuum shared from another
        # account the map can be encrypted with the uid of the logged-in account instead of the
        # home owner's.
        owner_ids = dict.fromkeys(uid for uid in (str(self._user_id), self._logged_in_user_id()) if uid)
        device_ids = dict.fromkeys([str(self._device_id), *MAP_KEY_FALLBACK_DEVICE_IDS])
        candidates = [
            (owner_id, device_id, hex_key)
            for owner_id in owner_ids for device_id in device_ids for hex_key in MAP_KEY_TYPES_HEX
        ]
        if self._map_key_ids is not None:
            candidates.insert(0, self._map_key_ids)
        return list(dict.fromkeys(candidates))

    def decode_and_parse(self, raw_map: bytes) -> MapData:
        last_error: Exception | None = None
        for owner_id, device_id, hex_key in self._map_key_id_candidates():
            key = map_key(self._wifi_info_sn, owner_id, device_id, self.model, self._mac, hex_key)
            try:
                decoded_map = unpack_map(raw_map, key, hex_key)
            except DECRYPT_ERRORS as err:
                last_error = err
                continue
            if (owner_id, device_id, hex_key) != self._map_key_ids:
                _LOGGER.debug("Map decrypted with the %s owner id, device id %s and the %s key",
                              "home" if owner_id == str(self._user_id) else "logged-in", device_id,
                              "digest" if hex_key else "short")
                self._map_key_ids = (owner_id, device_id, hex_key)
            return self.map_data_parser.parse(_with_current_map_info(decoded_map))

        # Keep the last map instead of making the entities unavailable.
        _LOGGER.warning("Failed to decrypt the %s map with any known key: %r", self.model, last_error)
        raise FailedMapParseException() from last_error

    def additional_data(self: Self) -> dict[str, Any]:
        super_data = super().additional_data()
        if self._wifi_info_sn is None:
            return super_data
        owner_id, device_id, hex_key = self._map_key_ids or (str(self._user_id), str(self._device_id), True)
        enc_key = map_key(self._wifi_info_sn, owner_id, device_id, self.model, self._mac, hex_key)
        return {**super_data, "enc_key": enc_key}


def map_key(wifi_sn: str, owner_id: str, device_id: str, model: str, mac: str, hex_key: bool) -> str:
    # vacuum_map_parser_ijai.aes_decryptor.gen_md5_key with the key type as a parameter instead of
    # taken from the parser's model list.
    model_suffix = model.split(".")[-1]
    if len(model_suffix) > 4:
        model_suffix = model_suffix[-4:]
    elif len(model_suffix) in (2, 3):
        model_suffix = model_suffix.rjust(4, "0")
    encrypted = aes_encrypt("+".join([wifi_sn, owner_id, device_id]), mac.lower().replace(":", "") + model_suffix)
    digest = MD5.new(encrypted.encode("utf-8")).hexdigest()
    return digest if hex_key else digest[8:-8].upper()


def unpack_map(raw_map: bytes, key: str, hex_key: bool) -> bytes:
    # IjaiMapDataParser.unpack_map with an explicit key.
    try:
        raw_map = base64.b64decode(raw_map, validate=True)
    except binascii.Error:
        pass
    return zlib.decompress(aes_decrypt(raw_map, key, hex_key))


def _with_current_map_info(decoded_map: bytes) -> bytes:
    # vacuum-map-parser-ijai looks the current map (mapHead.mapHeadId) up in mapInfo to name it and
    # fails with UnboundLocalError ('current_map') when it is not listed, which happens on maps
    # uploaded while cleaning. Add an unnamed entry so the rooms are still parsed.
    robot_map = RobotMap()
    robot_map.ParseFromString(decoded_map)
    map_id = robot_map.mapHead.mapHeadId
    if any(info.mapHeadId == map_id for info in robot_map.mapInfo):
        return decoded_map
    robot_map.mapInfo.add(mapHeadId=map_id)
    return robot_map.SerializeToString()
