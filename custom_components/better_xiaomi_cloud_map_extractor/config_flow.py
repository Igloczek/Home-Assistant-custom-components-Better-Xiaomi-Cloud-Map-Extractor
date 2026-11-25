"""Config flow: one handler for the domain, put together from the steps in `flow/`."""
from __future__ import annotations

from homeassistant.core import callback

from .const import DOMAIN
from .flow.login import LoginSteps
from .flow.original import OriginalImportSteps
from .flow.reauth import ReauthSteps
from .flow.vacuum import VacuumSteps
from .options_flow import XiaomiCloudMapExtractorOptionsFlowHandler
from .types import XiaomiCloudMapExtractorConfigEntry


# noinspection PyTypeChecker,PyBroadException
class XiaomiCloudMapExtractorFlowHandler(LoginSteps, ReauthSteps, OriginalImportSteps, VacuumSteps, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
            config_entry: XiaomiCloudMapExtractorConfigEntry) -> XiaomiCloudMapExtractorOptionsFlowHandler:
        """Get the options flow."""
        return XiaomiCloudMapExtractorOptionsFlowHandler()
