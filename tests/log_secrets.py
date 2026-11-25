"""The secrets the fakes hand out, and the check that none of them reached a log or a UI message.

Users paste debug logs, diagnostics and the "Failed to update" message into public issues (upstream
#778). Every test that scans for leaks uses this one list, so a secret added to the fakes is looked
for everywhere at once.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote, quote_plus
from collections.abc import Iterable, Mapping

import pytest

from .const import TOKEN
from .fake_xiaomi import CAPTCHA_ICK, IDENTITY_SESSION, NEW_SSECURITY, PASS_TOKEN, SSECURITY, VERIFY_CONTEXT

PASSWORD = "made-up-password"
# Query parameters of the signed map URL the fakes hand out; the signature must not be logged.
MAP_URL_SIGNATURE = "map-url-signature"
MAP_URL_QUERY = f"Expires=1&Signature={MAP_URL_SIGNATURE}"

SESSION_SECRETS = {
    "password": PASSWORD,
    "password hash": hashlib.md5(PASSWORD.encode()).hexdigest().upper(),
    "passToken": PASS_TOKEN,
    "ssecurity": SSECURITY,
    "renewed ssecurity": NEW_SSECURITY,
    "psecurity": "psecurity-value",
    "serviceToken": "-service-token",  # every token the fake issues ends like this
    "STS clientSign": "client-sign-value",
    "verification context": VERIFY_CONTEXT,
    "identity session": IDENTITY_SESSION,
    "captcha ick": CAPTCHA_ICK,
    "verification ticket": "end-ticket",
    "QR long-poll ticket": "qr-poll-ticket",
    "QR login ticket": "qr-login-ticket",
    "signed map URL": MAP_URL_SIGNATURE,
    "captcha image": "JFIF",
    "device token": TOKEN,
}


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    records = [*caplog.get_records("setup"), *caplog.get_records("call")]
    records += [record for record in caplog.records if record not in records]  # teardown, when checked there
    assert records, "the scenario logged nothing, so it proves nothing"
    return "\n".join(f"{record.getMessage()} {record.exc_text or ''}" for record in records)


def _forms(secret: str) -> set[str]:
    """The secret as written, and as it appears inside a URL's query string."""
    return {secret, quote(secret, safe=""), quote(secret), quote_plus(secret)}


def leaked(texts: Iterable[str], secrets: Mapping[str, str] | None = None) -> set[str]:
    text = "\n".join(texts)
    return {
        name for name, secret in {**SESSION_SECRETS, **(secrets or {})}.items()
        if secret and any(form in text for form in _forms(secret))
    }


def assert_no_secrets(caplog: pytest.LogCaptureFixture, secrets: Mapping[str, str] | None = None,
                      *messages: str) -> None:
    """No secret in the log at any level, nor in `messages` (what the UI shows, e.g. UpdateFailed)."""
    found = leaked([log_text(caplog), *messages], secrets)
    assert not found, f"secrets leaked: {sorted(found)}"
