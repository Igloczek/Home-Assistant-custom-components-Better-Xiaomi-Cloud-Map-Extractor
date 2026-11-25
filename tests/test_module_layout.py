"""Splitting a module must not change what users and Home Assistant see of it.

Users turn on debug logs per logger (`logger: logs: ...connector.xiaomi_cloud.connector: debug`), and
bug reports are read by logger name: the modules split out of one file keep logging under its name.
The config flow, put together from `flow/`, keeps every step id that flows in progress and the
translations refer to.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

import pytest
from homeassistant.config_entries import HANDLERS

from custom_components.better_xiaomi_cloud_map_extractor.config_flow import XiaomiCloudMapExtractorFlowHandler
from custom_components.better_xiaomi_cloud_map_extractor.const import DOMAIN

PACKAGE = "custom_components.better_xiaomi_cloud_map_extractor"
CLOUD = f"{PACKAGE}.connector.xiaomi_cloud"
TRANSLATIONS = Path(__file__).parents[1] / "custom_components/better_xiaomi_cloud_map_extractor/translations/en.json"

# Every step the config flow had before it was split into `flow/`.
STEPS = {
    "import", "reauth", "reauth_confirm", "reauth_captcha", "reauth_verification", "user", "auth_method_selection",
    "import_original", "integration_discovery", "import_original_confirm", "auth_saved_credentials",
    "auth_credentials", "auth_qr", "auth_qr_failed", "after_auth", "devices_failed", "select_vacuum",
    "confirm_data", "auth_captcha", "auth_two_factor",
}


def _modules(package: str) -> list[str]:
    path = importlib.import_module(package).__path__
    return [f"{package}.{info.name}" for info in pkgutil.iter_modules(path) if not info.ispkg]


@pytest.mark.parametrize("module", _modules(CLOUD))
def test_cloud_connector_logs_under_one_logger(module: str) -> None:
    logger = getattr(importlib.import_module(module), "_LOGGER", None)

    assert logger is None or logger.name == f"{CLOUD}.connector"


@pytest.mark.parametrize("module", _modules(f"{PACKAGE}.flow"))
def test_config_flow_steps_log_under_the_config_flow(module: str) -> None:
    logger = getattr(importlib.import_module(module), "_LOGGER", None)

    assert logger is None or logger.name == f"{PACKAGE}.config_flow"


def test_issues_log_under_repairs() -> None:
    from custom_components.better_xiaomi_cloud_map_extractor import issues

    assert issues._LOGGER.name == f"{PACKAGE}.repairs"


def test_config_flow_handler_keeps_every_step() -> None:
    translated = json.loads(TRANSLATIONS.read_text())["config"]["step"]

    for step in STEPS | set(translated):
        assert callable(getattr(XiaomiCloudMapExtractorFlowHandler, f"async_step_{step}", None)), step
    assert HANDLERS.get(DOMAIN) is XiaomiCloudMapExtractorFlowHandler
