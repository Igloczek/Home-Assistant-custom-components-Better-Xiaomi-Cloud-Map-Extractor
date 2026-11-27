"""The original's v2 YAML block (`camera: - platform: xiaomi_cloud_map_extractor`) left behind.

Users who came from v2 still have it in their YAML: the original only imported it into an entry.
Once that vacuum runs here and the original is uninstalled, Home Assistant logs "Platform error:
camera - Integration 'xiaomi_cloud_map_extractor' not found" at every start, and core raises no
repair for a missing platform. An instruction-only Repairs issue (`original_yaml_<entry_id>`) names
the file and line and shows the block with the credentials redacted so the user can delete it.

Home Assistant passes the whole configuration to `async_setup`; its dicts keep the file and line
they were loaded from (`__config_file__`, `__line__`), also from `!include` files and packages.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import Any

import yaml
from homeassistant.config import config_per_platform, extract_domain_configs
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PLATFORM, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, ORIGINAL_DOMAIN

REDACTED = "<redacted>"
_SECRET_KEYS = (CONF_USERNAME, CONF_PASSWORD, CONF_TOKEN)
# The original's v2 YAML was only ever a camera platform.
_PLATFORM_DOMAIN = "camera"


@dataclass(frozen=True, slots=True)
class OriginalYamlBlock:
    key: str  # "camera", or "camera 2" and so on
    config: dict[str, Any]
    file: str
    line: str

    def is_for(self, data: dict[str, Any]) -> bool:
        """Whether this block configures the vacuum of an entry with this data."""
        host, token = self.config.get(CONF_HOST), self.config.get(CONF_TOKEN)
        return (host is not None and host == data.get(CONF_HOST)) or (
            token is not None and token == data.get(CONF_TOKEN)
        )

    def placeholders(self) -> dict[str, str]:
        return {"file": self.file, "line": self.line, "snippet": self.snippet()}

    def snippet(self) -> str:
        redacted = {
            str(key): REDACTED if key in _SECRET_KEYS else _plain(value) for key, value in self.config.items()
        }
        # The platform first, as it is written.
        ordered = {CONF_PLATFORM: redacted.pop(CONF_PLATFORM), **redacted}
        text = yaml.dump({self.key: [ordered]}, Dumper=_Dumper, default_flow_style=None, sort_keys=False)
        return f"```yaml\n{text}```"


DATA_ORIGINAL_YAML: HassKey[list[OriginalYamlBlock]] = HassKey(f"{DOMAIN}_original_yaml")


class _Dumper(yaml.SafeDumper):
    """Indents list items under their key, as configuration.yaml is usually written."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Annotated strings (NodeStrClass) and anything else the dumper does not know.
    return str(value)


def _location(hass: HomeAssistant, item: Any) -> tuple[str, str]:
    file = getattr(item, "__config_file__", None)
    line = getattr(item, "__line__", None)
    if file is None:
        return "configuration.yaml", "?"
    with contextlib.suppress(ValueError):
        file = os.path.relpath(file, hass.config.config_dir)
    return file, "?" if line is None else str(line)


@callback
def async_find_original_yaml(hass: HomeAssistant, config: ConfigType) -> None:
    """At startup: remember every block of the original's camera platform in the configuration."""
    blocks = []
    for key in extract_domain_configs(config, _PLATFORM_DOMAIN):
        for platform, item in config_per_platform({key: config[key]}, _PLATFORM_DOMAIN):
            if platform != ORIGINAL_DOMAIN or not isinstance(item, dict):
                continue
            file, line = _location(hass, item)
            blocks.append(OriginalYamlBlock(str(key), dict(item), file, line))
    hass.data[DATA_ORIGINAL_YAML] = blocks


def original_yaml_block(hass: HomeAssistant, data: dict[str, Any]) -> OriginalYamlBlock | None:
    """The original's YAML block for the vacuum of an entry (of either integration) with this data."""
    return next((block for block in hass.data.get(DATA_ORIGINAL_YAML, []) if block.is_for(data)), None)


def original_yaml_issue_id(entry: ConfigEntry) -> str:
    return f"original_yaml_{entry.entry_id}"


@callback
def async_check_original_yaml(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise the issue when the original's YAML still configures this entry's vacuum."""
    if (block := original_yaml_block(hass, dict(entry.data))) is None:
        ir.async_delete_issue(hass, DOMAIN, original_yaml_issue_id(entry))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        original_yaml_issue_id(entry),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="original_yaml",
        translation_placeholders={"name": entry.title, **block.placeholders()},
    )
