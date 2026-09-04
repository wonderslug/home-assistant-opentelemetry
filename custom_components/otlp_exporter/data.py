"""Custom types for otlp_exporter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


type OTLPExporterConfigEntry = ConfigEntry[OTLPExporterData]


@dataclass
class OTLPExporterData:
    """Runtime data for the OTLP Exporter integration."""

    endpoint: str
