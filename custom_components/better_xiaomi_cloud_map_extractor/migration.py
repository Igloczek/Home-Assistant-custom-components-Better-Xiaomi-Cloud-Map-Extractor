"""Import setups from the original Xiaomi Cloud Map Extractor (`xiaomi_cloud_map_extractor`).

The fork has its own domain so it can be installed next to the original. A user who switches keeps
their setup: the config flow copies an original config entry (data, options, title) and its stored
cloud session, disables the original entry, and setup moves the original's device and entity
registry entries to this integration so entity ids, names and customisations stay the same.

The import works while the original is installed and loaded: Home Assistant only lets the user
uninstall it once its entries are deleted, and deleting an entry deletes the entities still linked
to it. The original's entry is only ever disabled (`disabled_by=user`, reversible), on the user's
explicit confirm; its data, options and storage are never changed or deleted. Entities and devices
are only taken from an original entry that cannot load them: disabled, or its integration removed.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .const import DOMAIN, ORIGINAL_DOMAIN, STORAGE_VERSION
from .original_yaml import original_yaml_block

_LOGGER = logging.getLogger(__name__)


async def async_original_installed(hass: HomeAssistant) -> bool:
    """Whether the original integration's code is installed, so it may load its entries."""
    try:
        await async_get_integration(hass, ORIGINAL_DOMAIN)
    except IntegrationNotFound:
        return False
    return True


def vacuum_mac(entry: ConfigEntry) -> str | None:
    """The MAC of the vacuum an entry (of either integration) is for, if the entry knows it.

    Entries created by a config flow have it as unique id. Entries created by the YAML import (the
    original's or this one's) have no unique id and only store it in `data`, or not at all when the
    import's cloud lookup failed.
    """
    if entry.unique_id is not None:
        return entry.unique_id
    if mac := entry.data.get(CONF_MAC):
        return format_mac(mac)
    return None


def unique_id_taken(hass: HomeAssistant, unique_id: str, entry_id: str | None = None) -> bool:
    """Whether an entry of this integration other than `entry_id` (ignored ones too) has this unique id."""
    return any(
        entry.unique_id == unique_id and entry.entry_id != entry_id
        for entry in hass.config_entries.async_entries(DOMAIN, include_ignore=True)
    )


@callback
def async_fill_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give an entry without unique id the stored MAC (`format_mac`), unless another entry has it.

    Entries created by the v2 YAML import of 0.1.0 and earlier have none.
    """
    if entry.unique_id is not None or not (mac := entry.data.get(CONF_MAC)):
        return
    if unique_id_taken(hass, mac := format_mac(mac), entry.entry_id):
        return
    hass.config_entries.async_update_entry(entry, unique_id=mac)


def importable_original_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Original config entries for vacuums that are not set up in this integration yet.

    An original entry without a MAC (YAML import whose cloud lookup failed) is matched by host, as
    the YAML import itself does.
    """
    ours = hass.config_entries.async_entries(DOMAIN, include_ignore=True)
    macs = {mac for entry in ours if (mac := vacuum_mac(entry)) is not None}
    hosts = {host for entry in ours if (host := entry.data.get(CONF_HOST)) is not None}
    return [
        entry
        for entry in hass.config_entries.async_entries(ORIGINAL_DOMAIN, include_ignore=False)
        if ((mac := vacuum_mac(entry)) is not None and mac not in macs)
        or (mac is None and entry.data.get(CONF_HOST) not in hosts)
    ]


def original_left_issue_id(original: ConfigEntry) -> str:
    return f"original_left_{original.entry_id}"


async def async_disable_original(hass: HomeAssistant, original: ConfigEntry) -> None:
    """Disable the original's entry so its device and entities can move here; waits for its unload.

    A Repairs issue then tells the user they can delete the disabled entry and uninstall the
    original; it goes away when the entry is deleted or enabled again (`async_original_changed`).
    """
    if original.disabled_by is None:
        await hass.config_entries.async_set_disabled_by(original.entry_id, ConfigEntryDisabler.USER)
    _async_create_original_left_issue(hass, original)


def async_flag_leftover_originals(hass: HomeAssistant) -> None:
    """At startup: raise the issue again for every disabled original entry imported here."""
    importable = importable_original_entries(hass)
    for original in hass.config_entries.async_entries(ORIGINAL_DOMAIN):
        if original.disabled_by is not None and original not in importable:
            _async_create_original_left_issue(hass, original)


def _async_create_original_left_issue(hass: HomeAssistant, original: ConfigEntry) -> None:
    """Tell the user what is left to switch; the original's YAML block goes before the uninstall.

    Uninstalling the original with its YAML block still there logs a platform error at every start.
    """
    placeholders = {"name": original.title}
    if (block := original_yaml_block(hass, dict(original.data))) is not None:
        placeholders |= {"file": block.file, "line": block.line}
    ir.async_create_issue(
        hass,
        DOMAIN,
        original_left_issue_id(original),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="original_left_yaml" if block is not None else "original_left",
        translation_placeholders=placeholders,
    )


def async_original_changed(hass: HomeAssistant, original: ConfigEntry, removed: bool) -> None:
    """Drop the leftover issue once the original's entry is deleted or enabled again."""
    if removed or original.disabled_by is None:
        ir.async_delete_issue(hass, DOMAIN, original_left_issue_id(original))


def _original_can_load(original: ConfigEntry, installed: bool) -> bool:
    return installed and original.disabled_by is None


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
    change. Originals are matched by the vacuum's MAC (`vacuum_mac`), so entries the original
    imported from YAML (no unique id) are found too. Only taken from an original entry that cannot
    load them (disabled, or the original removed), and never an entity that already exists here.

    Disabling the original entry marked its enabled entities and device `disabled_by=config_entry`;
    they are enabled again here, so each keeps the state it had in the original.
    """
    if (mac := vacuum_mac(entry)) is None:
        return
    originals = [
        original for original in hass.config_entries.async_entries(ORIGINAL_DOMAIN) if vacuum_mac(original) == mac
    ]
    if not originals:
        return
    installed = await async_original_installed(hass)
    originals = [original for original in originals if not _original_can_load(original, installed)]

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
            if registry_entry.disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY:
                registry.async_update_entity(registry_entry.entity_id, disabled_by=None)
            _LOGGER.info("Took over %s from %s", registry_entry.entity_id, ORIGINAL_DOMAIN)
        # A device belongs to one config entry; move it after its entities (moving it first would
        # drop entities still on the original entry) so its name, area and labels stay.
        for device in dr.async_entries_for_config_entry(devices, original.entry_id):
            identifiers = {
                (DOMAIN, value) if domain == ORIGINAL_DOMAIN else (domain, value)
                for domain, value in device.identifiers
            }
            disabled_by = None if device.disabled_by is dr.DeviceEntryDisabler.CONFIG_ENTRY else device.disabled_by
            try:
                devices.async_update_device(
                    device.id, new_config_entry_id=entry.entry_id, new_identifiers=identifiers, disabled_by=disabled_by
                )
            except (ValueError, dr.DeviceIdentifierCollisionError, dr.DeviceConnectionCollisionError) as err:
                _LOGGER.warning("Could not take over device %s: %s", device.name, err)
