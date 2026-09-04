# OTLP Exporter for Home Assistant

A Home Assistant custom component that exports telemetry (logs, metrics and
traces) to an OpenTelemetry Collector over OTLP.

Status: early scaffold. The config flow collects an OTLP endpoint; the actual
exporters (logs/metrics/traces) are not implemented yet. See
[`otel-ha-research.md`](./otel-ha-research.md) for the design research and
open questions behind this project.

## Installation

1. Copy `custom_components/otlp_exporter` into your Home Assistant
   `custom_components` directory (or install via HACS as a custom repository).
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for
   "OTLP Exporter".
4. Enter the URL of your OpenTelemetry Collector's OTLP endpoint.

## Development

This repo includes a devcontainer with a standalone Home Assistant instance
for local development.

1. Open the repository in the VS Code devcontainer.
2. Run `scripts/develop` to start Home Assistant with this component loaded.
3. Run `scripts/lint` before committing.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## License

MIT — see [`LICENSE`](./LICENSE).
