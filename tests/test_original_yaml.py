"""The original's v2 YAML block left in configuration.yaml after the import.

Users who came from v2 have `camera: - platform: xiaomi_cloud_map_extractor` in their YAML (the
original imported it into an entry). Once that vacuum runs here and the original is uninstalled,
Home Assistant logs "Platform error: camera - Integration 'xiaomi_cloud_map_extractor' not found" at
every start, and core raises no repair for a missing platform. A Repairs issue names the block,
shows it with the credentials redacted, and says removing it is safe.

The YAML is loaded from real files through Home Assistant's loader, so `!secret`, includes and the
file and line annotations behave as on an installation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant import config as conf_util
from homeassistant import config_entries
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN, ORIGINAL_DOMAIN

from .const import ENTRY_DATA, HOST, MAC, PASSWORD, TOKEN, USERNAME
from .original_entries import OPTIONS, YAML, add_original, install_original

ORIGINAL_BLOCK = f"""\
  - platform: xiaomi_cloud_map_extractor
    host: {HOST}
    token: !secret vacuum_token
    username: {USERNAME}
    password: !secret xiaomi_password
    country: de
    name: Living room map
    colors:
      color_map_wall: [23, 23, 23]
"""
SECRETS = f"vacuum_token: {TOKEN}\nxiaomi_password: {PASSWORD}\n"


async def _start(hass: HomeAssistant, tmp_path: Path, configuration: str) -> dict[str, Any]:
    """Load configuration.yaml like a start does and set up camera and this integration with it."""
    (tmp_path / "configuration.yaml").write_text(configuration)
    (tmp_path / "secrets.yaml").write_text(SECRETS)
    hass.config.config_dir = str(tmp_path)
    config = await conf_util.async_hass_config_yaml(hass)
    assert await async_setup_component(hass, "camera", config)
    assert await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()
    return config


def _imported_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Living room vacuum", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    return entry


def _yaml_issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"original_yaml_{entry.entry_id}")


async def test_leftover_original_yaml_after_uninstalling_the_original_raises_a_repair(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    """The maintainer's state: imported, original entry deleted and uninstalled, YAML still there."""
    entry = _imported_entry(hass)

    await _start(hass, tmp_path, f"homeassistant:\n\ncamera:\n{ORIGINAL_BLOCK}")

    # Rendering the block never stands in the way of the map.
    assert entry.state is ConfigEntryState.LOADED
    issue = _yaml_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "original_yaml"
    assert issue.severity is ir.IssueSeverity.WARNING
    placeholders = issue.translation_placeholders
    assert placeholders["name"] == "Living room vacuum"
    assert placeholders["file"] == "configuration.yaml"
    assert placeholders["line"] == "4"
    snippet = placeholders["snippet"]
    assert snippet.startswith("```yaml\ncamera:\n  - platform: xiaomi_cloud_map_extractor\n")
    assert f"host: {HOST}" in snippet
    assert "name: Living room map" in snippet
    assert "color_map_wall" in snippet
    for secret in (TOKEN, PASSWORD, USERNAME):
        assert secret not in snippet
    assert "token: <redacted>" in snippet


async def test_the_block_is_found_in_an_included_file(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    entry = _imported_entry(hass)
    block = "".join(line[2:] for line in ORIGINAL_BLOCK.splitlines(keepends=True))
    (tmp_path / "cameras.yaml").write_text(f"- platform: generic\n  still_image_url: http://example.com/a.jpg\n{block}")

    await _start(hass, tmp_path, "camera: !include cameras.yaml\n")

    placeholders = _yaml_issue(hass, entry).translation_placeholders
    assert (placeholders["file"], placeholders["line"]) == ("cameras.yaml", "3")
    assert "generic" not in placeholders["snippet"]


async def test_no_repair_without_the_originals_yaml(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    entry = _imported_entry(hass)

    await _start(hass, tmp_path, f"camera:\n{ORIGINAL_BLOCK.replace('xiaomi_cloud_map_extractor', DOMAIN)}")

    assert _yaml_issue(hass, entry) is None


async def test_no_repair_for_a_block_of_a_vacuum_that_is_not_set_up_here(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    """The original still runs that vacuum from its YAML: removing the block is not ours to say."""
    entry = _imported_entry(hass)
    other = ORIGINAL_BLOCK.replace(HOST, "192.168.1.77").replace("!secret vacuum_token", "ffffffffffffffff")

    await _start(hass, tmp_path, f"camera:\n{other}")

    assert _yaml_issue(hass, entry) is None
    assert [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN] == []


async def test_removing_the_entry_removes_the_repair(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    entry = _imported_entry(hass)
    await _start(hass, tmp_path, f"camera:\n{ORIGINAL_BLOCK}")
    assert _yaml_issue(hass, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _yaml_issue(hass, entry) is None


@pytest.mark.parametrize("installed", [False, True], ids=["original_not_installed", "original_installed"])
async def test_import_tells_about_the_yaml_before_the_uninstall(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, tmp_path: Path, installed: bool
) -> None:
    """Right after the import, both repairs are up, and the one saying "uninstall the original"
    says to remove the YAML block first, so nobody lands in the platform error."""
    original = add_original(hass, hass_storage, YAML)
    if installed:
        install_original(hass)
    await _start(hass, tmp_path, f"camera:\n{ORIGINAL_BLOCK}")
    assert [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN] == []

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"original_entry": original.entry_id})
    await hass.async_block_till_done()
    entry = result["result"]

    left = ir.async_get(hass).async_get_issue(DOMAIN, f"original_left_{original.entry_id}")
    assert left.translation_key == "original_left_yaml"
    assert left.translation_placeholders["file"] == "configuration.yaml"
    assert _yaml_issue(hass, entry) is not None

    # Deleting the original entry keeps the YAML repair: the block is still there.
    assert await hass.config_entries.async_remove(original.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"original_left_{original.entry_id}") is None
    assert _yaml_issue(hass, entry) is not None


async def test_original_left_without_yaml_keeps_its_text(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, tmp_path: Path
) -> None:
    original = add_original(hass, hass_storage, YAML)
    await _start(hass, tmp_path, "homeassistant:\n")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_original"})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"original_entry": original.entry_id})
    await hass.async_block_till_done()

    left = ir.async_get(hass).async_get_issue(DOMAIN, f"original_left_{original.entry_id}")
    assert left.translation_key == "original_left"


async def test_leftover_original_at_startup_mentions_the_yaml(
    hass: HomeAssistant, hass_storage: dict[str, Any], map_connector: MagicMock, tmp_path: Path
) -> None:
    """A restart after the import, original entry still disabled: the startup issue says it too."""
    original = add_original(hass, hass_storage, YAML)
    await hass.config_entries.async_set_disabled_by(original.entry_id, config_entries.ConfigEntryDisabler.USER)
    entry = _imported_entry(hass)

    await _start(hass, tmp_path, f"camera:\n{ORIGINAL_BLOCK}")

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"original_left_{original.entry_id}").translation_key == (
        "original_left_yaml"
    )
    assert _yaml_issue(hass, entry) is not None
    assert hass.config_entries.async_entries(ORIGINAL_DOMAIN) == [original]


async def _fix(hass: HomeAssistant, issue: ir.IssueEntry) -> dict[str, Any]:
    assert issue.is_fixable
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["file"] == "configuration.yaml"
    result = await manager.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    return result


async def test_fix_removes_the_block_and_keeps_the_rest(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    """One click: the block goes, a backup stays, the next start finds nothing and nothing else changed."""
    entry = _imported_entry(hass)
    configuration = f"homeassistant:\n  name: Home\n\n# Cameras\ncamera:\n{ORIGINAL_BLOCK}\nlogger:\n  default: info\n"
    await _start(hass, tmp_path, configuration)

    result = await _fix(hass, _yaml_issue(hass, entry))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _yaml_issue(hass, entry) is None
    expected = "homeassistant:\n  name: Home\n\n# Cameras\n\nlogger:\n  default: info\n"
    assert (tmp_path / "configuration.yaml").read_text() == expected
    backups = list(tmp_path.glob("configuration.yaml.better-xiaomi-backup-*"))
    assert [backup.read_text() for backup in backups] == [configuration]
    # Home Assistant loads the result, and the next start raises nothing.
    config = await conf_util.async_hass_config_yaml(hass)
    assert "camera" not in config
    assert config["logger"] == {"default": "info"}
    assert entry.state is ConfigEntryState.LOADED


async def test_fix_in_an_included_file_keeps_the_other_cameras(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    entry = _imported_entry(hass)
    unindented = ORIGINAL_BLOCK.replace("\n  ", "\n").removeprefix("  ")
    (tmp_path / "cameras.yaml").write_text(f"- platform: generic\n  name: Door\n  still_image_url: http://door/\n{unindented}")
    await _start(hass, tmp_path, "camera: !include cameras.yaml\n")
    issue = _yaml_issue(hass, entry)
    assert issue is not None

    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["description_placeholders"]["file"] == "cameras.yaml"
    result = await manager.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (tmp_path / "cameras.yaml").read_text() == "- platform: generic\n  name: Door\n  still_image_url: http://door/\n"
    assert (tmp_path / "configuration.yaml").read_text() == "camera: !include cameras.yaml\n"


async def test_fix_leaves_a_changed_file_alone_and_says_what_to_delete(
    hass: HomeAssistant, map_connector: MagicMock, tmp_path: Path
) -> None:
    """The file changed since the start and the block can't be told apart: nothing is written."""
    entry = _imported_entry(hass)
    await _start(hass, tmp_path, f"camera:\n{ORIGINAL_BLOCK}")
    changed = "camera: [{platform: xiaomi_cloud_map_extractor, host: !secret ip}]\n"
    (tmp_path / "configuration.yaml").write_text(changed)

    result = await _fix(hass, _yaml_issue(hass, entry))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "remove_by_hand"
    assert "- platform: xiaomi_cloud_map_extractor" in result["description_placeholders"]["snippet"]
    assert (tmp_path / "configuration.yaml").read_text() == changed
    assert not list(tmp_path.glob("*backup*"))
    assert _yaml_issue(hass, entry) is not None
