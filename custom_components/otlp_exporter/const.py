"""Constants for otlp_exporter."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "otlp_exporter"

CONF_ENDPOINT = "endpoint"
