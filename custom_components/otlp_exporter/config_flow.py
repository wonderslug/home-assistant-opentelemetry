"""Adds config flow for OTLP Exporter."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.helpers import selector
from slugify import slugify

from .const import DOMAIN


class OTLPExporterFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for OTLP Exporter."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        _errors = {}
        if user_input is not None:
            await self.async_set_unique_id(slugify(user_input[CONF_URL]))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_URL],
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL,
                        default=(user_input or {}).get(CONF_URL, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.URL,
                        ),
                    ),
                },
            ),
            errors=_errors,
        )
