"""Removing the original's leftover YAML block from the text of a configuration file.

Layouts seen in real configuration.yaml files: the block alone or among other cameras, indented or
not, with comments, `!secret` and nested options, in an `!include`d list file.
"""

from __future__ import annotations

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.original_yaml_removal import (
    OriginalYamlRemovalError,
    remove_block_text,
)

BLOCK = """\
  - platform: xiaomi_cloud_map_extractor
    host: 192.0.2.1
    token: !secret vacuum_token  # from Mi Home
    username: !secret xiaomi_user
    password: !secret xiaomi_password
    country: de
    draw: [all]
    colors:
      color_map_wall: [23, 23, 23]
    attributes:
      - calibration_points
"""


def test_only_camera_removes_the_camera_key() -> None:
    text = f"homeassistant:\n  name: Home\n\n# Vacuum map\ncamera:\n{BLOCK}\nsensor:\n  - platform: time_date\n"

    assert remove_block_text(text, 6) == "homeassistant:\n  name: Home\n\n# Vacuum map\n\nsensor:\n  - platform: time_date\n"


def test_other_cameras_stay() -> None:
    text = f"camera:\n  - platform: generic\n    name: Door\n{BLOCK}  - platform: ffmpeg\n    input: x\n"

    assert remove_block_text(text, 4) == "camera:\n  - platform: generic\n    name: Door\n  - platform: ffmpeg\n    input: x\n"


def test_block_at_the_end_of_the_file_without_newline() -> None:
    text = "camera:\n  - platform: generic\n    name: Door\n  - platform: xiaomi_cloud_map_extractor\n    host: 192.0.2.1"

    assert remove_block_text(text, 4) == "camera:\n  - platform: generic\n    name: Door\n"


def test_list_not_indented_under_its_key() -> None:
    text = "camera:\n- platform: xiaomi_cloud_map_extractor\n  host: 192.0.2.1\n  token: abc\nlogger:\n  default: info\n"

    assert remove_block_text(text, 2) == "logger:\n  default: info\n"


def test_numbered_camera_key() -> None:
    text = f"camera:\n  - platform: generic\n    name: Door\ncamera 2:\n{BLOCK}"

    assert remove_block_text(text, 5) == "camera:\n  - platform: generic\n    name: Door\n"


def test_included_list_file() -> None:
    text = f"# cameras.yaml\n{BLOCK}"

    assert remove_block_text(text, 2) == "# cameras.yaml\n[]\n"


def test_found_by_host_when_the_file_moved_lines() -> None:
    text = f"\n\n\ncamera:\n{BLOCK}"

    assert remove_block_text(text, 2, host="192.0.2.1") == "\n\n\n"


def test_found_by_token_when_the_host_is_a_secret() -> None:
    text = "camera:\n  - platform: xiaomi_cloud_map_extractor\n    host: !secret vacuum_ip\n    token: abc\n"

    assert remove_block_text(text, 40, host="192.0.2.1", token="abc") == ""


def test_two_blocks_remove_only_the_one_at_the_line() -> None:
    text = f"camera:\n{BLOCK}{BLOCK.replace('192.0.2.1', '192.0.2.2')}"

    assert remove_block_text(text, 2) == f"camera:\n{BLOCK.replace('192.0.2.1', '192.0.2.2')}"


@pytest.mark.parametrize(
    ("text", "line"),
    [
        # Not there any more.
        ("camera:\n  - platform: generic\n", 2),
        # A flow list can't be cut by lines.
        ("camera: [{platform: xiaomi_cloud_map_extractor, host: 192.0.2.1}]\n", 1),
        # Not YAML any more.
        ("camera:\n  - platform: xiaomi_cloud_map_extractor\n   host: [\n", 2),
    ],
)
def test_unsafe_layouts_are_left_to_the_user(text: str, line: int) -> None:
    with pytest.raises(OriginalYamlRemovalError):
        remove_block_text(text, line)
