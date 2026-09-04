"""
Custom integration to export Home Assistant telemetry via OTLP.

For more details about this integration, please refer to
https://github.com/wonderslug/home-assistant-opentelemetry
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_URL, Platform

from .const import LOGGER
from .data import OTLPExporterData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import OTLPExporterConfigEntry

PLATFORMS: list[Platform] = []


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: OTLPExporterConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    entry.runtime_data = OTLPExporterData(
        endpoint=entry.data[CONF_URL],
    )

    LOGGER.debug(
        "OTLP exporter configured for endpoint %s", entry.runtime_data.endpoint
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: OTLPExporterConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
