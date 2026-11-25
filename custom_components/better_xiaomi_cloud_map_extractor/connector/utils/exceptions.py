import re
from abc import ABC
from dataclasses import dataclass, field

# A URL's query string: aiohttp quotes the whole request URL in its errors ("Connection timeout to
# host https://...?signature=...&ssecurity=..."), and the encrypted API and STS calls carry the
# session's secrets there.
_URL_QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s?#'\"<>]*)\?[^\s'\"<>]*", re.IGNORECASE)


def redact_url_queries(text: str) -> str:
    """`text` with every URL's query string removed; the host and path stay for debugging."""
    return _URL_QUERY.sub(r"\1?<redacted>", text)


class XiaomiCloudMapExtractorException(Exception, ABC):
    pass


@dataclass
class FailedConnectionException(XiaomiCloudMapExtractorException):
    """Xiaomi could not be reached (timeout, DNS, connection reset). Never a reason to log in again."""
    base_exception: Exception

    def __post_init__(self) -> None:
        # Raised inside `except`: a logged traceback would print the original error, URL and all.
        self.__suppress_context__ = True

    def __str__(self) -> str:
        reason = redact_url_queries(str(self.base_exception))
        return f"Could not connect to Xiaomi Cloud: {type(self.base_exception).__name__} {reason}".strip()


class UnexpectedResponseError(Exception):
    """Xiaomi answered with something other than its API's JSON: a 5xx, an HTML error page, an undecodable body."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status

    def __str__(self) -> str:
        return f"unexpected response (HTTP {self.status})"


class RetryLaterException(XiaomiCloudMapExtractorException):
    """A password login failed moments ago; trying again now would only make Xiaomi ask for a captcha."""

    def __str__(self) -> str:
        return "Waiting before logging in to Xiaomi Cloud again"


class DeviceNotFoundException(XiaomiCloudMapExtractorException):
    pass


class InvalidCredentialsException(XiaomiCloudMapExtractorException):
    pass


class FailedLoginException(XiaomiCloudMapExtractorException):
    pass


class SessionExpiredException(FailedLoginException):
    """Xiaomi Cloud rejected the stored serviceToken; the session has to be renewed."""

    def __str__(self) -> str:
        return "Xiaomi Cloud session expired"


class InvalidDeviceTokenException(XiaomiCloudMapExtractorException):
    pass


class FailedMapDownloadException(XiaomiCloudMapExtractorException):
    pass


class FailedMapParseException(XiaomiCloudMapExtractorException):
    pass


class MapSavingDisabledException(XiaomiCloudMapExtractorException):
    """The vacuum reports map saving off in the Xiaomi Home app, so the cloud has no usable map."""

    def __str__(self) -> str:
        return "Map saving is turned off on the vacuum. Turn on map saving in the Xiaomi Home app"


class InvalidTwoFactorCodeException(FailedLoginException):
    """Xiaomi did not accept the verification code (wrong, outdated, or the verification expired)."""

    def __str__(self) -> str:
        return "Xiaomi did not accept the verification code"


class VerificationIncompleteException(FailedLoginException):
    """Xiaomi accepted the verification code, but its redirects did not end in a session."""

    def __str__(self) -> str:
        return "Xiaomi accepted the verification code but did not complete the login"


@dataclass
class EncryptedMapUnsupportedException(XiaomiCloudMapExtractorException):
    """The cloud serves this model's map encrypted, and how to decrypt it for this model is not known."""

    model: str

    def __str__(self) -> str:
        return f"Xiaomi Cloud sends the map of {self.model} encrypted, and this integration cannot decrypt it yet"


@dataclass
class TwoFactorAuthRequiredException(XiaomiCloudMapExtractorException):
    url: str
    headers: dict[str, str] = field(repr=False)
    context: str = field(repr=False)
    # How Xiaomi sends the code (4 phone, 8 email) and whether it did: without a code, only the link helps.
    flag: int = 8
    code_sent: bool = True

    def __str__(self) -> str:
        return "Xiaomi requires additional identity verification"


@dataclass
class CaptchaRequiredException(XiaomiCloudMapExtractorException):
    url: str
    image_data: bytes = field(repr=False)

    def __str__(self) -> str:
        return "Xiaomi requires a captcha to log in"


class QrLoginTimeoutException(FailedLoginException):
    """The QR code expired before the login was approved in the Xiaomi Home app."""

    def __str__(self) -> str:
        return "The QR code was not scanned in time"
