"""manifest.json on master carries the last released version.

Home Assistant shows the manifest version. The release workflow writes the tag's version into the zip,
but a manual install from the repository showed upstream's "v3.0.0-beta", which reads newer than every
release and confuses bug reports (HACS default review, hacs/default#14982).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "custom_components" / "better_xiaomi_cloud_map_extractor" / "manifest.json"


def test_manifest_version_is_the_last_release_in_the_changelog() -> None:
    released = re.findall(r"^## (\d+\.\d+\.\d+) - ", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)

    assert json.loads(MANIFEST.read_text())["version"] == released[0]
