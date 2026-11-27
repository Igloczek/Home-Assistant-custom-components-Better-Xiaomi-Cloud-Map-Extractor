from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import (
    CONF_TOKEN,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_MAC,
    CONF_HOST,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant

from .types import XiaomiCloudMapExtractorConfigEntry

# Diagnostics get attached to public issues (upstream #778): no credentials, device token, host,
# MAC, map decryption key or signed map URL. The Xiaomi session (`store.py`) is never included.
TO_REDACT_ENTRY = {CONF_HOST, CONF_NAME, CONF_TOKEN, CONF_PASSWORD, CONF_USERNAME, CONF_MAC, "unique_id", "title"}
TO_REDACT_DEVICE = {"enc_key", "last_used_url"}


async def async_get_config_entry_diagnostics(
        hass: HomeAssistant, entry: XiaomiCloudMapExtractorConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    return {
        "config_entry_data": async_redact_data(entry.as_dict(), TO_REDACT_ENTRY),
        "device_data": (
            async_redact_data(coordinator.data.as_dict(), TO_REDACT_DEVICE)
            if coordinator.data is not None
            else None
        ),
    }
