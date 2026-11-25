"""Logging in: the menu, password login with captcha and two-factor, and QR code login."""
from __future__ import annotations

import base64
import logging
from asyncio import Task
from typing import Any, Self

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigFlowResult, SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .base import FlowBase
from .errors import login_error
from ..connector.utils.exceptions import (
    TwoFactorAuthRequiredException,
    CaptchaRequiredException,
    FailedLoginException,
    FailedConnectionException,
    QrLoginTimeoutException,
    InvalidCredentialsException,
    InvalidTwoFactorCodeException,
    VerificationIncompleteException,
)
from ..connector.xiaomi_cloud.connector import XiaomiCloudConnector
from ..const import CONF_CAPTCHA_CODE, CONF_TWO_FACTOR_CODE
from ..migration import importable_original_entries

# Logged under the config flow's name, as before the split, so logger settings keep applying.
_LOGGER = logging.getLogger(f"{__package__.rpartition('.')[0]}.config_flow")


class LoginSteps(FlowBase):
    """Every way to log in ends in `_after_auth` (vacuum.py)."""

    def _saved_credentials(self) -> tuple[str, str] | None:
        if self.source != SOURCE_REAUTH:
            return None
        data = self._get_reauth_entry().data
        username, password = data.get(CONF_USERNAME), data.get(CONF_PASSWORD)
        if not username or not password:
            return None
        return username, password

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        return await self.async_step_auth_method_selection()

    async def async_step_auth_method_selection(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu_options = ["auth_credentials", "auth_qr"]
        if self.source == SOURCE_USER and importable_original_entries(self.hass):
            menu_options.insert(0, "import_original")
        if self._saved_credentials() is not None:
            menu_options.insert(0, "auth_saved_credentials")
        return self.async_show_menu(
            step_id="auth_method_selection",
            menu_options=menu_options,
        )

    async def async_step_auth_saved_credentials(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in again with the username and password already stored in the entry."""
        username, password = self._saved_credentials()
        return await self.async_step_auth_credentials({CONF_USERNAME: username, CONF_PASSWORD: password})

    async def async_step_auth_credentials(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._username = user_input.get(CONF_USERNAME)
            self._password = user_input.get(CONF_PASSWORD)

            def session_creator() -> ClientSession:
                return async_create_clientsession(self.hass)

            self._connector = XiaomiCloudConnector(session_creator)
            try:
                if await self._connector.login_with_credentials(self._username, self._password) is None:
                    errors["base"] = "cloud_login_error"
            except CaptchaRequiredException as e:
                self._captcha_exception = e
                return await self.async_step_auth_captcha()
            except TwoFactorAuthRequiredException as e:
                self._two_factor_exception = e
                return await self.async_step_auth_two_factor()
            except Exception as e:  # noqa: BLE001 - every error becomes a message on the form
                errors["base"] = login_error(e)

            if not errors:
                return await self._after_auth()
        return self._credentials_form(errors)

    def _credentials_form(self: Self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="auth_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=self._username or vol.UNDEFINED): str,
                    vol.Required(CONF_PASSWORD): str
                }
            ),
            errors=errors
        )

    async def async_step_auth_qr(self: Self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show a QR code and wait for the login to be approved in the Xiaomi Home app.

        Home Assistant runs this step again while it waits (the placeholders must survive that), and a
        progress step may only end with `async_show_progress_done`: failures go to `auth_qr_failed`.
        """
        if self._wait_for_login_task is None:
            def session_creator() -> ClientSession:
                return async_create_clientsession(self.hass)

            self._connector = XiaomiCloudConnector(session_creator)
            self._username = None
            self._password = None
            try:
                qr_image, login_link = await self._connector.login_with_qr_get_code()
            except FailedConnectionException:
                self._qr_error = "cannot_connect"
                return await self.async_step_auth_qr_failed()
            except FailedLoginException:
                self._qr_error = "qr_code_unavailable"
                return await self.async_step_auth_qr_failed()
            self._qr_placeholders = {
                "qr_image_b64": base64.b64encode(qr_image).decode("utf-8"),
                "login_link": login_link,
            }
            self._wait_for_login_task = self.hass.async_create_task(
                self._connector.login_with_qr_wait_for_completion()
            )

        if not self._wait_for_login_task.done():
            return self.async_show_progress(
                step_id="auth_qr",
                progress_action="wait_for_login",
                description_placeholders=self._qr_placeholders,
                progress_task=self._wait_for_login_task,
            )

        task, self._wait_for_login_task = self._wait_for_login_task, None
        self._qr_error = self._qr_login_error(task)
        if self._qr_error is not None:
            return self.async_show_progress_done(next_step_id="auth_qr_failed")
        return self.async_show_progress_done(next_step_id="after_auth")

    @staticmethod
    def _qr_login_error(task: Task) -> str | None:
        if task.cancelled():
            return "qr_timeout"
        error = task.exception()
        if error is None:
            return None
        if isinstance(error, QrLoginTimeoutException):
            return "qr_timeout"
        if isinstance(error, FailedConnectionException):
            return "cannot_connect"
        if isinstance(error, FailedLoginException):
            return "qr_rejected"
        # A bug, not something Xiaomi answered: keep the traceback, still let the user try again.
        _LOGGER.error("Unexpected error during the QR code login", exc_info=error)
        return "qr_failed"

    async def async_step_auth_qr_failed(
            self: Self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The QR login did not finish; submitting shows a new code."""
        if user_input is not None:
            return await self.async_step_auth_qr()
        return self.async_show_form(step_id="auth_qr_failed", errors={"base": self._qr_error or "qr_failed"})

    async def async_step_auth_captcha(
            self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Captcha verification step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            captcha_code = user_input[CONF_CAPTCHA_CODE]
            try:
                await self._connector.continue_login_with_captcha(captcha_code)
                return await self._after_auth()
            except CaptchaRequiredException as e:
                errors[CONF_CAPTCHA_CODE] = "invalid_captcha"
                self._captcha_exception = e
            except TwoFactorAuthRequiredException as e:
                self._two_factor_exception = e
                return await self.async_step_auth_two_factor()
            except InvalidCredentialsException:
                # The captcha was right, the password is not: only the credentials form can fix that (#712).
                return self._credentials_form({"base": "cloud_login_error"})
            except Exception as e:  # noqa: BLE001 - every error becomes a message on the form
                errors["base"] = login_error(e)
        captcha_image_data = self._captcha_exception.image_data
        captcha_image_b64 = base64.b64encode(captcha_image_data).decode("utf-8")

        return self.async_show_form(
            step_id="auth_captcha",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CAPTCHA_CODE
                    ): str
                }
            ),
            errors=errors,
            description_placeholders={
                "captcha_image_b64": captcha_image_b64
            },
        )

    async def async_step_auth_two_factor(
            self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Two-factor auth verification step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            two_factor_code = (user_input.get(CONF_TWO_FACTOR_CODE) or "").strip()
            if not two_factor_code:
                # Verified in the browser through the link instead: Xiaomi should let the login through now.
                return await self.async_step_auth_credentials(
                    {CONF_USERNAME: self._username, CONF_PASSWORD: self._password}
                )
            try:
                if not await self._connector.continue_login_with_two_factor(
                        two_factor_code, self._two_factor_exception):
                    raise VerificationIncompleteException()
                return await self._after_auth()
            except TwoFactorAuthRequiredException as e:
                self._two_factor_exception = e
                return await self.async_step_auth_two_factor()
            except InvalidTwoFactorCodeException:
                errors[CONF_TWO_FACTOR_CODE] = "invalid_two_factor_code"
            except VerificationIncompleteException:
                errors["base"] = "verification_incomplete"
            except Exception as e:  # noqa: BLE001 - every error becomes a message on the form
                errors["base"] = login_error(e)
        elif not self._two_factor_exception.code_sent:
            # Xiaomi did not send a code (it wanted a captcha first, or refused): the link is the way.
            errors["base"] = "two_factor_code_not_sent"

        return self.async_show_form(
            step_id="auth_two_factor",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TWO_FACTOR_CODE
                    ): str
                }
            ),
            errors=errors,
            description_placeholders={"verification_url": self._two_factor_exception.url},
        )
