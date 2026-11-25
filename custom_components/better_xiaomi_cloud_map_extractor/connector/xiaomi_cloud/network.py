"""How the cloud connector tells "Xiaomi could not be reached" from "Xiaomi said no"."""
from __future__ import annotations
import asyncio

from aiohttp import ClientError

from ..utils.exceptions import FailedConnectionException, UnexpectedResponseError

# Errors that mean "Xiaomi could not be reached", as opposed to "Xiaomi said no".
# They must never lead to a password login: that is what makes Xiaomi ask for a captcha.
NETWORK_ERRORS = (ClientError, asyncio.TimeoutError, TimeoutError, OSError)


def _is_outage(status: int) -> bool:
    """Statuses that mean Xiaomi, or a proxy in front of it, is down or busy: never a refusal."""
    return status >= 500 or status in (408, 429)


def _unavailable(status: int) -> FailedConnectionException:
    """Xiaomi answered, but not with its API: retry later like after a timeout, never log in again."""
    return FailedConnectionException(UnexpectedResponseError(status))
