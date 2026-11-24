"""Import setups from the original Xiaomi Cloud Map Extractor (`xiaomi_cloud_map_extractor`).

The fork has its own domain so it can be installed next to the original. A user who switches keeps
their setup: the config flow copies an original config entry (data, options, title) and its stored
cloud session, and setup moves the original's entity registry entries to this integration so entity
ids, names and customisations stay the same.

Nothing here runs while the original integration is installed: its entries and entities are never
touched while it can load them.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .const import DOMAIN, ORIGINAL_DOMAIN, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


async def async_original_installed(hass: HomeAssistant) -> bool:
    """Whether the original integration's code is installed, so it may load its entries."""
    try:
        await async_get_integration(hass, ORIGINAL_DOMAIN)
    except IntegrationNotFound:
        return False
    return True


def importable_original_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Original config entries for vacuums that are not set up in this integration yet."""
    configured = {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN, include_ignore=True)}
    return [
        entry
        for entry in hass.config_entries.async_entries(ORIGINAL_DOMAIN, include_ignore=False)
        if entry.unique_id is not None and entry.unique_id not in configured
    ]


async def async_copy_original_session(hass: HomeAssistant, mac: str) -> None:
    """Copy the original's stored cloud session for this vacuum, if there is one.

    The original file `.storage/xiaomi_cloud_map_extractor_<mac>` is left in place.
    """
    original = Store(hass, STORAGE_VERSION, f"{ORIGINAL_DOMAIN}_{format_mac(mac)}")
    if (data := await original.async_load()) is None:
        return
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}_{format_mac(mac)}").async_save(data)


async def async_adopt_original_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move the original's devices and entities for this vacuum to `entry`, keeping their ids.

    Runs at setup before the platforms add entities. Entity unique ids (`<device_id>_<entity
    domain>_<key>`) are the same in both integrations, so only the platform and config entry
    change. Skipped while the original integration is installed, and for any entity that is
    loaded or already exists in this integration.
    """
    originals = [
        original
        for original in hass.config_entries.async_entries(ORIGINAL_DOMAIN)
        if original.unique_id is not None and original.unique_id == entry.unique_id
    ]
    if not originals or await async_original_installed(hass):
        return

    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    for original in originals:
        for registry_entry in er.async_entries_for_config_entry(registry, original.entry_id):
            if registry_entry.platform != ORIGINAL_DOMAIN:
                continue
            if registry.async_get_entity_id(registry_entry.domain, DOMAIN, registry_entry.unique_id) is not None:
                continue
            try:
                registry.async_update_entity_platform(
                    registry_entry.entity_id,
                    DOMAIN,
                    new_config_entry_id=entry.entry_id,
                )
            except ValueError as err:
                _LOGGER.warning("Could not take over %s: %s", registry_entry.entity_id, err)
                continue
            _LOGGER.info("Took over %s from %s", registry_entry.entity_id, ORIGINAL_DOMAIN)
        # A device belongs to one config entry; move it after its entities (moving it first would
        # drop entities still on the original entry) so its name, area and labels stay.
        for device in dr.async_entries_for_config_entry(devices, original.entry_id):
            identifiers = {
                (DOMAIN, value) if domain == ORIGINAL_DOMAIN else (domain, value)
                for domain, value in device.identifiers
            }
            try:
                devices.async_update_device(
                    device.id, new_config_entry_id=entry.entry_id, new_identifiers=identifiers
                )
            except (ValueError, dr.DeviceIdentifierCollisionError, dr.DeviceConnectionCollisionError) as err:
                _LOGGER.warning("Could not take over device %s: %s", device.name, err)
