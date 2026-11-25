"""The locale the cloud API is called with comes from the environment, like Python's old `getdefaultlocale`."""

from __future__ import annotations

import warnings

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

LOCALE_VARIABLES = ("LC_ALL", "LC_CTYPE", "LANG", "LANGUAGE")


def _connector() -> XiaomiCloudConnector:
    return XiaomiCloudConnector(lambda: None, server="de")


@pytest.fixture(autouse=True)
def _clear_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LOCALE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "en_GB"),
        ({"LANG": "pl_PL.UTF-8"}, "pl_PL"),
        ({"LANG": "C"}, "en_GB"),
        ({"LANG": "POSIX"}, "en_GB"),
        # macOS sets this; Python 3.13 raised ValueError on the lower-case spelling.
        ({"LC_CTYPE": "UTF-8"}, "en_GB"),
        ({"LC_CTYPE": "utf-8"}, "en_GB"),
        # What Home Assistant's container sets; kept as Python 3.13 reported it.
        ({"LANG": "C.UTF-8"}, "C"),
        ({"LANG": "pl_PL.UTF-8", "LC_ALL": "de_DE.UTF-8"}, "de_DE"),
        ({"LANGUAGE": "fr_FR:en"}, "fr_FR"),
    ],
)
def test_locale_from_the_environment(monkeypatch: pytest.MonkeyPatch, environment: dict, expected: str) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert _connector()._locale == expected


def test_no_deprecated_locale_call() -> None:
    """`locale.getdefaultlocale` is deprecated and goes away in Python 3.15."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _connector()
