"""Fixtures for testing the OTLP Exporter integration."""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.otlp_exporter.const import DOMAIN

MOCK_ENDPOINT = "http://otel-collector.local:4318"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Load the integration from custom_components in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the OTLP Exporter integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=MOCK_ENDPOINT,
        data={CONF_URL: MOCK_ENDPOINT},
        unique_id="http-otel-collector-local-4318",
    )
