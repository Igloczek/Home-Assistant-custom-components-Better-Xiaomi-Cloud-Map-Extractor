"""Splitting a module must not move its log messages to another logger.

Users turn on debug logs per logger (`logger: logs: ...connector.xiaomi_cloud.connector: debug`), and
bug reports are read by logger name. The modules split out of one file keep logging under its name.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

PACKAGE = "custom_components.better_xiaomi_cloud_map_extractor"
CLOUD = f"{PACKAGE}.connector.xiaomi_cloud"


def _modules(package: str) -> list[str]:
    path = importlib.import_module(package).__path__
    return [f"{package}.{info.name}" for info in pkgutil.iter_modules(path) if not info.ispkg]


@pytest.mark.parametrize("module", _modules(CLOUD))
def test_cloud_connector_logs_under_one_logger(module: str) -> None:
    logger = getattr(importlib.import_module(module), "_LOGGER", None)

    assert logger is None or logger.name == f"{CLOUD}.connector"
