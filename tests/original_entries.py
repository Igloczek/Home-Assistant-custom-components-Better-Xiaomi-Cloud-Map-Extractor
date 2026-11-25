"""What the original Xiaomi Cloud Map Extractor v3.0.0-alpha-24 (`688fe3e`) leaves behind.

An original config entry comes in one of these shapes, depending on how the user set it up:

- `config_flow`: the original's UI flow (`async_step_confirm_data`); unique_id = MAC.
- `yaml`: the original's import of v2 YAML (`async_step_import` with
  `legacy.create_config_entry_data_from_yaml`): source `import`, **no unique_id**, title
  "Xiaomi Cloud Map Extractor", the MAC only in `data`, and `used_map_api` None unless the YAML set
  `force_api`. This is how every user who came from v2 YAML has their vacuum set up.
- `yaml_without_mac`: the same import when its cloud lookup failed: device_id, model, MAC and name
  are None. The original never sets it up (`to_configuration` raises `ConfigEntryAuthFailed`), so
  it has no entities, no device and no stored session.

Values are made up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, mock_platform

from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.const import ORIGINAL_DOMAIN

from .const import ENTRY_DATA, HOST, MAC, PASSWORD, TOKEN, USERNAME

OPTIONS = {
    "image_config": XiaomiCloudMapExtractorFlowHandler._default_image_config(),
    "colors": XiaomiCloudMapExtractorFlowHandler._default_colors(),
    "room_colors": {"16": [10, 20, 30, 255]},
    "drawables": ["charger", "path", "room_names"],
    "sizes": {"charger_radius": 6},
    "texts": [],
}
# Shape written by store.save_connector_config (XiaomiCloudConnectorConfig); values are made up.
SESSION = {
    "connector_config": {
        "username": "user@example.com",
        "password": "made-up-password",
        "server": "de",
        "user_id": "2",
        "c_user_id": "c2",
        "service_token": "made-up-service-token",
        "expiration": "2030-01-01T00:00:00+00:00",
        "ssecurity": "made-up-ssecurity",
        "device_id": "ABCDEFGHIJKLMNOP",
    }
}

# Every entity alpha-24 registers for a vacuum (camera, image, sensor, binary_sensor, button,
# switch), as (entity domain, key). Unique ids are `<device_id>_<entity domain>_<key>`.
ALPHA24_ENTITIES = [
    ("camera", "live_map"),
    ("image", "live_map"),
    *(
        ("sensor", key)
        for key in (
            "no_go_areas",
            "charger_position",
            "vacuum_position",
            "vacuum_room_id",
            "vacuum_room_name",
            "no_carpet_areas",
            "no_mopping_areas",
            "cleaned_rooms_ids",
            "goto_position",
            "goto_path",
            "goto_predicted_path",
            "mop_path",
            "path",
            "obstacles",
            "ignored_obstacles",
            "obstacles_with_photo",
            "ignored_obstacles_with_photo",
            "walls",
            "zones",
            "map_name",
            "rooms",
        )
    ),
    ("binary_sensor", "is_map_empty"),
    ("button", "force_update"),
    ("switch", "auto_update"),
]
# Enabled on the maintainer's Home Assistant; the rest are disabled by the integration.
ENABLED = {("image", "live_map"), ("sensor", "rooms")}

YAML_TITLE = "Xiaomi Cloud Map Extractor"


@dataclass(frozen=True)
class OriginalShape:
    name: str
    source: str
    unique_id: str | None
    title: str
    data: dict[str, Any]
    has_session: bool
    options: dict[str, Any] = field(default_factory=lambda: OPTIONS)

    @property
    def mac(self) -> str | None:
        return self.data["mac"]

    @property
    def device_id(self) -> str | None:
        return self.data["device_id"]

    @property
    def has_entities(self) -> bool:
        return self.device_id is not None


CONFIG_FLOW = OriginalShape(
    name="config_flow",
    source=config_entries.SOURCE_USER,
    unique_id=MAC,
    title="Living room vacuum",
    data=ENTRY_DATA,
    has_session=True,
)
# Exactly the keys `create_config_entry_data_from_yaml` writes (alpha-24 legacy.py).
YAML = OriginalShape(
    name="yaml",
    source=config_entries.SOURCE_IMPORT,
    unique_id=None,
    title=YAML_TITLE,
    data={
        "host": HOST,
        "token": TOKEN,
        "device_id": "428789378",
        "model": "viomi.vacuum.v8",
        "mac": MAC,
        "name": "Living room vacuum",
        "username": USERNAME,
        "password": PASSWORD,
        "server": "de",
        "used_map_api": None,
    },
    # The original's YAML import logs in but never stores the session.
    has_session=False,
)
YAML_WITHOUT_MAC = OriginalShape(
    name="yaml_without_mac",
    source=config_entries.SOURCE_IMPORT,
    unique_id=None,
    title=YAML_TITLE,
    data={
        "host": HOST,
        "token": TOKEN,
        "device_id": None,
        "model": None,
        "mac": None,
        "name": None,
        "username": USERNAME,
        "password": PASSWORD,
        "server": None,
        "used_map_api": None,
    },
    has_session=False,
)
SHAPES_WITH_MAC = [CONFIG_FLOW, YAML]
ALL_SHAPES = [CONFIG_FLOW, YAML, YAML_WITHOUT_MAC]


def add_original(hass: HomeAssistant, hass_storage: dict[str, Any], shape: OriginalShape) -> MockConfigEntry:
    """Add an original entry with the registry entries, device and session file alpha-24 leaves.

    Entity unique ids are the same in both integrations; only the registry platform differs. The
    device is renamed and one entity id customised by the user, which must both survive the import.
    """
    original = MockConfigEntry(
        domain=ORIGINAL_DOMAIN,
        source=shape.source,
        unique_id=shape.unique_id,
        title=shape.title,
        data=shape.data,
        options=shape.options,
    )
    original.add_to_hass(hass)
    if shape.has_entities:
        devices = dr.async_get(hass)
        device = devices.async_get_or_create(
            config_entry_id=original.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, shape.mac)},
            identifiers={(ORIGINAL_DOMAIN, shape.device_id)},
            name="Living room vacuum",
        )
        devices.async_update_device(device.id, name_by_user="Robot")
        registry = er.async_get(hass)
        for domain, key in ALPHA24_ENTITIES:
            registry.async_get_or_create(
                domain,
                ORIGINAL_DOMAIN,
                f"{shape.device_id}_{domain}_{key}",
                config_entry=original,
                device_id=device.id,
                suggested_object_id=f"living_room_vacuum_{key}",
                disabled_by=None if (domain, key) in ENABLED else er.RegistryEntryDisabler.INTEGRATION,
            )
        room = registry.async_get_entity_id("sensor", ORIGINAL_DOMAIN, f"{shape.device_id}_sensor_vacuum_room_name")
        registry.async_update_entity(room, new_entity_id="sensor.robot_room")
    if shape.has_session:
        key = f"{ORIGINAL_DOMAIN}_{shape.mac}"
        hass_storage[key] = {"version": 1, "key": key, "data": SESSION}
    return original


class _OriginalConfigFlow(config_entries.ConfigFlow, domain=ORIGINAL_DOMAIN):
    """alpha-24's config flow version, so Home Assistant loads its entries without migrating them."""

    VERSION = 1


def install_original(hass: HomeAssistant) -> None:
    """Install a stand-in for the original integration that loads its entries like alpha-24 does.

    Setup registers the vacuum's device and its 26 entities under the original's platform (as the
    original's platforms do; existing registry entries are reused) and puts the enabled ones in the
    state machine; unload removes those states. An entry without a device id fails setup with
    `ConfigEntryAuthFailed`, as alpha-24's `to_configuration` does.
    """
    loaded: dict[str, list[str]] = {}

    async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        if (device_id := entry.data["device_id"]) is None:
            raise ConfigEntryAuthFailed
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, entry.data["mac"])},
            identifiers={(ORIGINAL_DOMAIN, device_id)},
            name=entry.data["name"],
        )
        registry = er.async_get(hass)
        states = loaded.setdefault(entry.entry_id, [])
        for domain, key in ALPHA24_ENTITIES:
            registry_entry = registry.async_get_or_create(
                domain,
                ORIGINAL_DOMAIN,
                f"{device_id}_{domain}_{key}",
                config_entry=entry,
                device_id=device.id,
                suggested_object_id=f"living_room_vacuum_{key}",
                disabled_by=None if (domain, key) in ENABLED else er.RegistryEntryDisabler.INTEGRATION,
            )
            if not registry_entry.disabled:
                hass.states.async_set(registry_entry.entity_id, "idle")
                states.append(registry_entry.entity_id)
        return True

    async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        for entity_id in loaded.pop(entry.entry_id, []):
            hass.states.async_remove(entity_id)
        return True

    mock_integration(
        hass,
        MockModule(ORIGINAL_DOMAIN, async_setup_entry=async_setup_entry, async_unload_entry=async_unload_entry),
    )
    mock_platform(hass, f"{ORIGINAL_DOMAIN}.config_flow")
