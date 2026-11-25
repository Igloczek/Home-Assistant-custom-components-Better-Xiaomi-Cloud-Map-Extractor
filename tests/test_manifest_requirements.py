"""Everything the integration and its map parsers import is installed by manifest.json.

Home Assistant installs only the manifest's requirements and what those declare. A package that a
parser imports without declaring it (vacuum-map-parser-ijai and protobuf) works in the test venv and
on installations where another integration happens to bring it, and fails setup everywhere else.
"""

from __future__ import annotations

import ast
import json
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "better_xiaomi_cloud_map_extractor"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text())


def _closure(names: list[str]) -> set[str]:
    """Distributions installed for these requirements, with everything they declare."""
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = metadata.distribution(todo.pop()).metadata["Name"].lower().replace("_", "-")
        if name in seen:
            continue
        seen.add(name)
        for requirement in metadata.requires(name) or []:
            parsed = Requirement(requirement)
            if parsed.marker is not None and not parsed.marker.evaluate({"extra": ""}):
                continue
            try:
                metadata.distribution(parsed.name)
            except metadata.PackageNotFoundError:
                continue
            todo.append(parsed.name)
    return seen


def _imports(files: list[Path]) -> set[str]:
    modules = set()
    for file in files:
        # Top-level imports only: those run on import; ones in try blocks or functions are optional.
        for node in ast.parse(file.read_text()).body:
            if isinstance(node, ast.Import):
                modules |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def _files(requirement: str) -> list[Path]:
    distribution = metadata.distribution(Requirement(requirement).name)
    return [
        Path(distribution.locate_file(f))
        for f in distribution.files or []
        # Their own test suites import pytest; nothing loads them at runtime.
        if f.suffix == ".py" and not {"tests", "SelfTest"} & set(f.parts) and not f.name.startswith("test_")
        and not f.stem.endswith(("_test", "_benchmark"))
    ]


def test_every_import_is_installed_by_the_manifest() -> None:
    requirements = MANIFEST["requirements"]
    installed = _closure([Requirement(r).name for r in requirements] + ["homeassistant"])
    providers = metadata.packages_distributions()
    files = list(INTEGRATION.rglob("*.py")) + [f for r in requirements for f in _files(r)]

    missing = {}
    for module in _imports(files) - set(sys.stdlib_module_names):
        distributions = {d.lower().replace("_", "-") for d in providers.get(module, [])}
        if distributions and not distributions & installed:
            missing[module] = sorted(distributions)

    assert not missing, f"imported but not installed through manifest.json: {missing}"
