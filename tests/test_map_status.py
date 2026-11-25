"""The map update's status for each failure, and which failures reach the coordinator.

Sensors and repairs read the status; the coordinator turns raised failures into reauth or retry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.better_xiaomi_cloud_map_extractor.connector import XiaomiCloudMapExtractorConnector
from custom_components.better_xiaomi_cloud_map_extractor.connector.model import (
    XiaomiCloudMapExtractorConnectorStatus as Status,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.utils.exceptions import (
    DeviceNotFoundException,
    EncryptedMapUnsupportedException,
    FailedLoginException,
    FailedMapDownloadException,
    FailedMapParseException,
    InvalidCredentialsException,
    InvalidDeviceTokenException,
    MapSavingDisabledException,
    TwoFactorAuthRequiredException,
)
from custom_components.better_xiaomi_cloud_map_extractor.connector.xiaomi_cloud.connector import XiaomiCloudConnector

TWO_FACTOR = TwoFactorAuthRequiredException("https://account.xiaomi.com/verify", {}, "context")

FAILURES = {
    "device not found": (DeviceNotFoundException(), Status.DEVICE_NOT_FOUND, True),
    "invalid credentials": (InvalidCredentialsException(), Status.INVALID_CREDENTIALS, True),
    "failed login": (FailedLoginException(), Status.FAILED_LOGIN, True),
    "vacuum token": (InvalidDeviceTokenException(), Status.INVALID_TOKEN, True),
    "two-factor": (TWO_FACTOR, Status.TWO_FACTOR_REQUIRED, True),
    "map download": (FailedMapDownloadException(), Status.FAILED_MAP_DOWNLOAD, False),
    "map parsing": (FailedMapParseException(), Status.FAILED_MAP_PARSING, False),
    "map saving off, no map yet": (MapSavingDisabledException(), Status.MAP_SAVING_DISABLED, True),
    "map encrypted, no map yet": (EncryptedMapUnsupportedException("dreame.vacuum.md1808"), Status.MAP_ENCRYPTED, True),
}


def _extractor(error: Exception) -> XiaomiCloudMapExtractorConnector:
    extractor = XiaomiCloudMapExtractorConnector(MagicMock(), MagicMock(), None)
    extractor._cloud.cloud_connector = MagicMock(spec=XiaomiCloudConnector)
    extractor._cloud.cloud_connector.has_service_token.return_value = True
    extractor._cloud.cloud_connector.is_session_expiring.return_value = False
    extractor._vacuum_connector = MagicMock()
    extractor._vacuum_connector.get_map = AsyncMock(side_effect=error)
    extractor._status = Status.OK
    return extractor


@pytest.mark.parametrize(("error", "status", "raised"), FAILURES.values(), ids=FAILURES.keys())
async def test_failure_sets_the_status(error: Exception, status: Status, raised: bool) -> None:
    extractor = _extractor(error)

    if raised:
        with pytest.raises(type(error)):
            await extractor.get_data()
        data = extractor._map_cache
    else:
        data = await extractor.get_data()

    assert data.status is status
    assert data.last_update_timestamp is not None
    if error is TWO_FACTOR:
        assert data.two_factor_url == TWO_FACTOR.url
