"""The form error a failed login or device list shows: what the user can do about it."""
from __future__ import annotations

import logging

from aiohttp import ClientError

from ..connector.utils.exceptions import (
    XiaomiCloudMapExtractorException,
    FailedLoginException,
    FailedConnectionException,
    RetryLaterException,
)

# Logged under the config flow's name, as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__.rpartition('.')[0]}.config_flow")

# Errors that mean Xiaomi could not be reached, whether or not the connector wrapped them.
CONNECTION_ERRORS = (FailedConnectionException, RetryLaterException, ClientError, TimeoutError)

def login_error(error: Exception) -> str:
    """The form error for a failed login: what the user can do about it."""
    if isinstance(error, CONNECTION_ERRORS):
        return "cannot_connect"
    if isinstance(error, XiaomiCloudMapExtractorException):
        return "cloud_login_error"
    _LOGGER.error("Xiaomi answered the login in an unexpected way", exc_info=error)
    return "unexpected_response"


def device_list_error(error: Exception) -> str:
    """The form error for a device list that failed right after logging in."""
    if isinstance(error, CONNECTION_ERRORS):
        return "cannot_connect"
    if isinstance(error, FailedLoginException):
        return "session_rejected"
    _LOGGER.error("Xiaomi answered the device list in an unexpected way", exc_info=error)
    return "unexpected_response"
