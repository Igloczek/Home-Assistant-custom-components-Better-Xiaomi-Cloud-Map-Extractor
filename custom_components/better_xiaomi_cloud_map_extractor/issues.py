"""Issues: failures that need the user, raised once they keep happening and cleared once fixed.

- `wrong_map_format`: a vacuum whose map fails in the Xiaomi format gets its vendor's format.
- `map_format_upgrade`: a vacuum added while its model was unsupported gets the format this version
  reads it in. Checked at every setup, so it shows up right after the update that adds the format.
- `device_not_found`, `vacuum_token`, `map_download_failing`: the same failure FAILURE_THRESHOLD map
  updates in a row. The fix looks the vacuum up in every Xiaomi region with the stored session and
  offers what the cloud knows (region, IP address, token); a login is asked for only when the
  session can't be renewed or the account doesn't have the vacuum. Every issue goes away once the
  map works again.
- `map_saving_disabled`: an Ijai vacuum reports map saving off in the Xiaomi Home app, FAILURE_THRESHOLD
  map updates in a row. Only the app can fix it, so the issue just says so; the next good map clears it.
- `map_encrypted`: the cloud serves the map of this model encrypted with a scheme the integration does
  not know (Dreame md1808, upstream #450). Nothing the user changes fixes it, so the issue names the
  model and where support for it is tracked; the next good map clears it.
- `vacuum_unreachable`: the vacuum has not answered on the local network for LAN_UNREACHABLE_UPDATES
  updates. Same fix as a refused token (IP address and token); it goes away once the vacuum answers,
  since cloud maps can keep working without it.

The fix flows for these issues are in `repairs.py`.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MODEL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.hass_dict import HassKey
from miio import DeviceException

from .connector.model import XiaomiCloudMapExtractorConnectorStatus as Status
from .connector.model import XiaomiCloudMapExtractorData
from .connector.utils.exceptions import (
    DeviceNotFoundException,
    FailedConnectionException,
    InvalidDeviceTokenException,
    EncryptedMapUnsupportedException,
    MapSavingDisabledException,
)
from .connector.vacuums.base.model import VacuumApi
from .const import CONF_SERVER, CONF_USED_MAP_API, DOMAIN

# Logged under the repairs module's name, as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__}.repairs")

WRONG_MAP_FORMAT = "wrong_map_format"
MAP_FORMAT_UPGRADE = "map_format_upgrade"
DEVICE_NOT_FOUND = "device_not_found"
VACUUM_TOKEN = "vacuum_token"
MAP_DOWNLOAD_FAILING = "map_download_failing"
VACUUM_UNREACHABLE = "vacuum_unreachable"
MAP_SAVING_DISABLED = "map_saving_disabled"
MAP_ENCRYPTED = "map_encrypted"
MAP_FAILURES = (DEVICE_NOT_FOUND, VACUUM_TOKEN, MAP_DOWNLOAD_FAILING, MAP_SAVING_DISABLED, MAP_ENCRYPTED)
PERSISTENT_FAILURES = (*MAP_FAILURES, VACUUM_UNREACHABLE)
# Fixed outside Home Assistant: the issue explains, it has no fix flow.
UNFIXABLE_FAILURES = (MAP_SAVING_DISABLED, MAP_ENCRYPTED)
# Where support for a model is tracked, for the failures only a new release can fix.
ISSUES_URL = "https://github.com/Igloczek/better-xiaomi-cloud-map-extractor/issues"
# Fixed with the vacuum's own IP address and token, which can be typed in without the cloud.
LAN_FAILURES = (VACUUM_TOKEN, VACUUM_UNREACHABLE)
# Map updates in a row that fail the same way before it is worth asking the user.
FAILURE_THRESHOLD = 3
# Map updates in a row without an answer from the vacuum on the local network: 5 minutes at the
# default interval, so a Wi-Fi drop or a restart of the vacuum raises nothing.
LAN_UNREACHABLE_UPDATES = 30
DATA_FAILURES: HassKey[dict[str, dict[str, int]]] = HassKey(f"{DOMAIN}_failures")


@callback
def async_clear_entry_state(hass: HomeAssistant, entry_id: str) -> None:
    """Drop per-entry failure counters when an entry is removed."""
    if (counters := hass.data.get(DATA_FAILURES)) is not None:
        counters.pop(entry_id, None)


def wrong_map_format_issue_id(entry_id: str) -> str:
    return f"{WRONG_MAP_FORMAT}_{entry_id}"


def issue_ids(entry_id: str) -> list[str]:
    """Every issue this integration may raise for an entry."""
    return [f"{key}_{entry_id}" for key in (WRONG_MAP_FORMAT, MAP_FORMAT_UPGRADE, *PERSISTENT_FAILURES)]


def persistent_failure(outcome: Exception | Status) -> str | None:
    """Which persistent failure an update outcome counts towards, if any."""
    if isinstance(outcome, DeviceNotFoundException):
        return DEVICE_NOT_FOUND
    if isinstance(outcome, InvalidDeviceTokenException) or (
        isinstance(outcome, FailedConnectionException) and isinstance(outcome.base_exception, DeviceException)
    ):
        return VACUUM_TOKEN
    if outcome is Status.FAILED_MAP_DOWNLOAD:
        return MAP_DOWNLOAD_FAILING
    if isinstance(outcome, MapSavingDisabledException) or outcome is Status.MAP_SAVING_DISABLED:
        return MAP_SAVING_DISABLED
    if isinstance(outcome, EncryptedMapUnsupportedException) or outcome is Status.MAP_ENCRYPTED:
        return MAP_ENCRYPTED
    return None


@callback
def async_report_update(
    hass: HomeAssistant, entry: ConfigEntry, outcome: Exception | XiaomiCloudMapExtractorData
) -> None:
    """Counts failures in a row and raises an issue once they keep happening; clears it once fixed."""
    counters = hass.data.setdefault(DATA_FAILURES, {})
    previous = counters.get(entry.entry_id, {})
    counts: dict[str, int] = {}
    if isinstance(outcome, XiaomiCloudMapExtractorData):
        if outcome.lan_reachable is None and VACUUM_UNREACHABLE in previous:
            counts[VACUUM_UNREACHABLE] = previous[VACUUM_UNREACHABLE]  # not asked: nothing new
        elif outcome.lan_reachable is False:
            counts[VACUUM_UNREACHABLE] = previous.get(VACUUM_UNREACHABLE, 0) + 1
        elif outcome.lan_reachable:
            ir.async_delete_issue(hass, DOMAIN, f"{VACUUM_UNREACHABLE}_{entry.entry_id}")
        if outcome.status is Status.OK:
            for key in MAP_FAILURES:
                ir.async_delete_issue(hass, DOMAIN, f"{key}_{entry.entry_id}")
        outcome = outcome.status
    elif VACUUM_UNREACHABLE in previous:
        counts[VACUUM_UNREACHABLE] = previous[VACUUM_UNREACHABLE]  # the cloud failed: nothing new
    if (failure := persistent_failure(outcome)) is not None:
        counts[failure] = previous.get(failure, 0) + 1
    counters[entry.entry_id] = counts
    for failure, count in counts.items():
        threshold = LAN_UNREACHABLE_UPDATES if failure == VACUUM_UNREACHABLE else FAILURE_THRESHOLD
        if count >= threshold:
            _async_raise(hass, entry, failure, count)


def _async_raise(hass: HomeAssistant, entry: ConfigEntry, failure: str, count: int) -> None:
    issue_id = f"{failure}_{entry.entry_id}"
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        _LOGGER.warning("%s: %s for %d map updates in a row, see Settings → Repairs", entry.title,
                        failure.replace("_", " "), count)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=failure not in UNFIXABLE_FAILURES,
        # A vacuum Home Assistant can't reach may be on another network on purpose: not an error.
        severity=ir.IssueSeverity.WARNING if failure == VACUUM_UNREACHABLE else ir.IssueSeverity.ERROR,
        translation_key=failure,
        translation_placeholders={
            "name": entry.title,
            "server": str(entry.data.get(CONF_SERVER)),
            "host": str(entry.data.get(CONF_HOST)),
            "model": str(entry.data.get(CONF_MODEL)),
            "issues_url": ISSUES_URL,
        },
        data={"entry_id": entry.entry_id, "failure": failure},
    )


def _better_format(entry: ConfigEntry) -> VacuumApi | None:
    """The detected format of a vacuum set to the Xiaomi one, when its vendor has its own."""
    if entry.data.get(CONF_USED_MAP_API) != VacuumApi.XIAOMI:
        return None
    detected = VacuumApi.detect(entry.data.get(CONF_MODEL, ""))
    if detected in (None, VacuumApi.XIAOMI, VacuumApi.UNSUPPORTED):
        return None
    return detected


@callback
def async_check_map_format(hass: HomeAssistant, entry: ConfigEntry, status: Status) -> None:
    """Offer the vendor's map format after the Xiaomi one failed; withdraw it once the map works.

    Only a real parse failure raises the issue, so a vacuum whose map works in the Xiaomi format
    is never touched.
    """
    async_check_format_upgrade(hass, entry)
    issue_id = wrong_map_format_issue_id(entry.entry_id)
    if status is Status.OK:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if status is not Status.FAILED_MAP_PARSING or (detected := _better_format(entry)) is None:
        return

    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        _LOGGER.warning(
            "The map of %s (%s) could not be read in the Xiaomi map format; this model uses the %s "
            "format. Open Settings → Repairs to switch it.",
            entry.title, entry.data[CONF_MODEL], detected.title(),
        )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=WRONG_MAP_FORMAT,
        translation_placeholders={
            "name": entry.title,
            "model": entry.data[CONF_MODEL],
            "used_api": VacuumApi.XIAOMI.title(),
            "detected_api": detected.title(),
        },
        data={"entry_id": entry.entry_id, "detected_api": detected.value},
    )


def _new_format(entry: ConfigEntry) -> VacuumApi | None:
    """The format this version reads a vacuum in that was stored as unsupported, if there is one."""
    stored = entry.data.get(CONF_USED_MAP_API)
    if not stored or VacuumApi.known(stored) is not VacuumApi.UNSUPPORTED:
        return None
    detected = VacuumApi.detect(entry.data.get(CONF_MODEL, ""))
    if detected in (None, VacuumApi.UNSUPPORTED):
        return None
    return detected


@callback
def async_warn_unknown_format(entry: ConfigEntry) -> None:
    """A stored format this version doesn't know is shown as unsupported; the entry keeps it."""
    stored = entry.data.get(CONF_USED_MAP_API)
    if stored and VacuumApi.known(stored) is None:
        _LOGGER.warning(
            "%s (%s) is set to the %s map format, which this version doesn't know; its map is shown "
            "as unsupported",
            entry.title, entry.data.get(CONF_MODEL), stored,
        )


@callback
def async_check_format_upgrade(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer the format a newer release added for a vacuum stored as unsupported; withdraw it otherwise."""
    issue_id = f"{MAP_FORMAT_UPGRADE}_{entry.entry_id}"
    if (detected := _new_format(entry)) is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        _LOGGER.warning(
            "This version reads the map of %s (%s) in the %s format. Open Settings → Repairs to switch it.",
            entry.title, entry.data[CONF_MODEL], detected.title(),
        )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=MAP_FORMAT_UPGRADE,
        translation_placeholders={
            "name": entry.title,
            "model": entry.data[CONF_MODEL],
            "detected_api": detected.title(),
        },
        data={"entry_id": entry.entry_id, "detected_api": detected.value},
    )
