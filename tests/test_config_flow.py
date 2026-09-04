"""Test the OTLP Exporter config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL
from homeassistant.data_entry_flow import FlowResultType

from custom_components.otlp_exporter.const import DOMAIN

from .conftest import MOCK_ENDPOINT

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The user flow stores the endpoint it was given."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: MOCK_ENDPOINT},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_ENDPOINT
    assert result["data"] == {CONF_URL: MOCK_ENDPOINT}


async def test_user_flow_aborts_on_duplicate_endpoint(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Configuring the same endpoint twice aborts the second flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_URL: MOCK_ENDPOINT},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
