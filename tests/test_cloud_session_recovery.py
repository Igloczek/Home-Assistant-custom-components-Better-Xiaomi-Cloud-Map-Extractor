"""CloudSession: the session the map updates use, tested at its own interface.

The update cycle tests (test_session_lifecycle.py, test_transient_errors.py) drive it through the
map update against the fake cloud; these cover the cases that are hard to reach from there.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory

from custom_components.better_xiaomi_cloud_map_extractor.connector.cloud_session import (
    PASSWORD_LOGIN_RETRY_INTERVAL,
    RENEWAL_RETRY_INTERVAL,
    CloudSession,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    FailedConnectionException,
    FailedLoginException,
    InvalidCredentialsException,
    RetryLaterException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector


def _config(username: str | None = "user@example.com", password: str | None = "made-up-password") -> MagicMock:
    return MagicMock(username=username, password=password, server="de")


def _session(
    *, has_token: bool = True, expiring: bool = False, pass_token: bool = True, config: MagicMock | None = None
) -> tuple[CloudSession, MagicMock, AsyncMock]:
    saver = AsyncMock()
    session = CloudSession(MagicMock(), config or _config(), None, saver)
    connector = MagicMock(spec=XiaomiCloudConnector)
    connector.has_service_token.return_value = has_token
    connector.is_session_expiring.return_value = expiring
    connector.has_pass_token.return_value = pass_token
    session.cloud_connector = connector
    return session, connector, saver


async def test_working_session_is_used_as_is() -> None:
    session, connector, saver = _session()

    assert await session.connect() is connector

    connector.renew_session.assert_not_called()
    connector.login_with_credentials.assert_not_called()
    saver.assert_not_awaited()


async def test_expiring_session_renewal_refused_is_retried_an_hour_later(freezer: FrozenDateTimeFactory) -> None:
    session, connector, saver = _session(expiring=True)
    connector.renew_session = AsyncMock(return_value=False)

    await session.connect()
    await session.connect()
    assert connector.renew_session.await_count == 1

    freezer.tick(RENEWAL_RETRY_INTERVAL)
    await session.connect()
    assert connector.renew_session.await_count == 2
    saver.assert_not_awaited()


async def test_expiring_session_is_kept_while_xiaomi_cannot_be_reached() -> None:
    session, connector, _ = _session(expiring=True)
    connector.renew_session = AsyncMock(side_effect=FailedConnectionException(TimeoutError()))

    assert await session.connect() is connector
    connector.login_with_credentials.assert_not_called()


async def test_renewed_session_is_stored() -> None:
    session, connector, saver = _session(has_token=False)
    connector.renew_session = AsyncMock(return_value=True)

    await session.connect()

    saver.assert_awaited_once_with(connector.to_config.return_value)
    connector.login_with_credentials.assert_not_called()


async def test_without_stored_credentials_no_password_login_is_tried() -> None:
    session, connector, _ = _session(pass_token=False, config=_config(password=None))

    with pytest.raises(FailedLoginException):
        await session.recover()
    connector.login_with_credentials.assert_not_called()


async def test_failed_password_login_waits_before_the_next_one(freezer: FrozenDateTimeFactory) -> None:
    session, connector, _ = _session(pass_token=False)
    connector.login_with_credentials = AsyncMock(side_effect=InvalidCredentialsException())

    with pytest.raises(InvalidCredentialsException):
        await session.recover()
    with pytest.raises(RetryLaterException):
        await session.recover()
    assert connector.login_with_credentials.await_count == 1

    freezer.tick(PASSWORD_LOGIN_RETRY_INTERVAL + timedelta(seconds=1))
    connector.login_with_credentials = AsyncMock(return_value="service-token")
    await session.recover()
    connector.login_with_credentials.assert_awaited_once()


async def test_login_without_a_service_token_is_a_failed_login(caplog: pytest.LogCaptureFixture) -> None:
    session, connector, saver = _session(has_token=False, pass_token=False)
    connector.login_with_credentials = AsyncMock(return_value=None)

    with pytest.raises(FailedLoginException):
        await session.recover()
    assert "Not authenticated!" in caplog.text
    saver.assert_not_awaited()


async def test_session_that_cannot_be_stored_is_still_used(caplog: pytest.LogCaptureFixture) -> None:
    session, connector, saver = _session(has_token=False)
    connector.renew_session = AsyncMock(return_value=True)
    saver.side_effect = OSError("disk full")

    with caplog.at_level(logging.WARNING):
        assert await session.connect() is connector
    assert "Failed to store the Xiaomi Cloud session: disk full" in caplog.text


async def test_without_a_saver_a_new_session_is_only_kept_in_memory() -> None:
    session = CloudSession(MagicMock(), _config(), None)
    connector = MagicMock(spec=XiaomiCloudConnector)
    connector.has_service_token.return_value = False
    connector.has_pass_token.return_value = True
    connector.renew_session = AsyncMock(return_value=True)
    session.cloud_connector = connector

    await session.connect()

    connector.to_config.assert_not_called()


def test_logs_under_the_update_loop() -> None:
    from custom_components.better_xiaomi_cloud_map_extractor.connector import cloud_session

    assert cloud_session._LOGGER.name == "custom_components.better_xiaomi_cloud_map_extractor.connector"
