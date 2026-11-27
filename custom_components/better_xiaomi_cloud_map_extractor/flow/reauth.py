"""Reauthentication: tells the user what the map update ran into, then logs in again."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlowResult

from .base import FlowBase
from ..coordinator import AuthIssue


class ReauthSteps(FlowBase):

    async def async_step_reauth(
            self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an authentication error or missing cloud credentials."""
        self._reauth_issue = self._background_auth_issue()
        reason = self._reauth_issue.reason if self._reauth_issue else None
        if reason == "captcha_required":
            return await self.async_step_reauth_captcha()
        if reason == "verification_required" and self._reauth_issue.verification_url:
            return await self.async_step_reauth_verification()
        return await self.async_step_reauth_confirm()

    def _background_auth_issue(self) -> AuthIssue | None:
        """What the map update ran into, when the entry is loaded far enough to know."""
        runtime_data = getattr(self._get_reauth_entry(), "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        return getattr(coordinator, "auth_issue", None)

    async def async_step_reauth_confirm(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is not None:
            return await self.async_step_auth_method_selection()
        return self.async_show_form(step_id="reauth_confirm", description_placeholders=self._entry_placeholders())

    async def async_step_reauth_captcha(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Xiaomi asked for a captcha when the map update tried to log in again."""
        if user_input is not None:
            return await self.async_step_auth_method_selection()
        return self.async_show_form(step_id="reauth_captcha", description_placeholders=self._entry_placeholders())

    async def async_step_reauth_verification(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Xiaomi asked to verify the account (email/SMS) when the map update tried to log in again."""
        if user_input is not None:
            return await self.async_step_auth_method_selection()
        return self.async_show_form(
            step_id="reauth_verification",
            description_placeholders={
                **self._entry_placeholders(),
                "verification_url": self._reauth_issue.verification_url,
            },
        )

    def _entry_placeholders(self) -> dict[str, str]:
        return {"name": self._get_reauth_entry().title}
