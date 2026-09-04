# OpenTelemetry for Home Assistant — Research Notes

Status: research complete, no code written yet.
Verified against `home-assistant/core` @ `dev` and `open-telemetry/opentelemetry-python` @ `main`, September 2026.
Target instance: HAOS 18.2, Core 2026.8.3, Supervisor 2026.08.0, generic-x86-64.

Goal: emit OTel logs, metrics and traces from as much of Home Assistant as
possible, exporting to a self-hosted OpenTelemetry Collector (contrib), with
storage chosen downstream.

---

## 1. Prior art

### Exists

- **`rhizomatics/remote_logger`** — the only purpose-built OTLP integration for HA.
  HACS default store, v2.0.4. Logs only.
  - OTLP/HTTP only (protobuf or JSON), no gRPC.
  - Hooks the root Python logger or `system_log_event`, so multi-line logs and
    stacktraces stay single records with `code.file.path` / `code.line.number` /
    `exception.stacktrace` attributes.
  - Can forward arbitrary HA events (state changes, service calls, lifecycle) as
    log records. Already subscribes to `EVENT_CALL_SERVICE`,
    `EVENT_STATE_CHANGED`, `EVENT_AUTOMATION_TRIGGERED`, `EVENT_SCRIPT_STARTED`.
  - **`"requirements": []`** — hand-rolled OTLP/JSON, zero external deps.
  - Covers HA Core + custom components only. Not add-ons, Supervisor or HAOS host.
  - Already installed on the target instance, not yet configured.

### Does not exist

- No OTel metrics exporter for HA (core or HACS).
- No OTel trace support of any kind.
- No OpenTelemetry Collector add-on for HAOS, in any repository.

### Community demand

Essentially nil. One forum thread (Nov 2024, "Is HomeAssistant creating traces
with OpenTelemetry?") with two posts and no developer response. remote_logger's
announcement thread (Mar 2026). The `supernotify` project documents OTLP event
export as a recipe. No architecture discussion, no core PR, no feature request
with traction.

Closest analogue elsewhere: openHAB has an active proposal (Jul 2026) for an
OTel-based observability layer across core/distro/openhabian, deliberately
structured so instrumentation does not hard-depend on the OTel libraries. Worth
reading before writing a proposal — same design arguments already fought.

---

## 2. Deployment constraint (HAOS)

Home Assistant does not support running third-party containers on HAOS. Doing so
marks the system unsupported; Supervisor logs the containers as errors and some
mark it unhealthy. The Portainer add-on's own documentation says it is for
debugging HA's own containers, not deploying user software.

So the Collector must run either:

1. **On another host on the LAN** — zero risk, simplest, or
2. **As a local add-on** in `/addons` wrapping `otel/opentelemetry-collector-contrib`
   — Supervisor-managed, backed up, watchdog. Target instance already runs one
   local add-on (Insteon MQTT) plus Samba, Studio Code Server and Terminal & SSH,
   so the workflow is established.

### Feeds into the Collector

| Source | Receiver | Notes |
|---|---|---|
| remote_logger | `otlp` (4318) | HA core logs + events |
| HA `influxdb` integration | `influxdb` | accepts line protocol at `/write` and `/api/v2/write`; ignores `db`/`rp`/`org`/`bucket` params |
| HA `prometheus` integration | `prometheus` | scrape `/api/prometheus` with LLAT bearer; brings `entity_info`/`area_info`/`floor_info` join metrics |
| LogSpout add-on | `syslog` | add-on, Supervisor and host logs |

The `influxdb` receiver path is notable: the target instance already runs the
InfluxDB and Grafana add-ons, so HA's `influxdb` integration can be repointed at
the Collector, converted to OTel, and fanned back out via the `influxdb`
exporter — Grafana keeps working, reversible in one line, no token or scrape
config needed.

---

## 3. Traces from automations and scripts

### The mapping

`ActionTrace.run_id` comes from `homeassistant.util.uuid.random_uuid_hex()`,
which is literally `f"{getrandbits(128):032x}"` — 128 random bits as 32 hex
chars. That is exactly a W3C trace-id. HA generates usable trace IDs already,
and they are surfaced in the automation editor and websocket API, so spans can
cross-reference the HA UI by the same string.

| HA | OTel |
|---|---|
| `run_id` (128-bit hex) | `trace_id`, verbatim |
| `ActionTrace._timestamp_start` / `_timestamp_finish` | root span start/end |
| `TraceElement.path` (`trigger/0`, `action/2/choose/1`) | child span name |
| `TraceElement._error`, `_template_errors` | span status + exception events |
| `_script_execution` (`finished`, `aborted`, `cancelled`, …) | root span status |
| `TraceElement.child_id` (key + run_id) | parent→child span across runs |
| `Context.parent_id` | causal link between separate runs |

`ScriptTrace` subclasses `ActionTrace`, so scripts come free.

### The hook seam

`trace_automation` and `trace_script` are context managers that call
`trace.finished()` in a `finally:` block. By then `_trace` is fully populated —
`set_trace(trace_get())` hands over the live dict mutated in place during the
run. Wrapping `ActionTrace.finished()` yields a complete, correct trace at the
exact moment it closes, no polling.

There is **no callback or event** for trace completion. A custom component must
monkeypatch. See §7.

### Why in-process is mandatory

`stored_traces` defaults to **5 per automation**, held in an in-memory
`LimitedSizeDict`, persisted to `.storage/trace.saved_traces` only at
`EVENT_HOMEASSISTANT_STOP`. Any out-of-process approach polling `trace/list` and
`trace/get` will silently drop runs on busy automations.

### Hard problems

1. **No span IDs, no per-step end times.** `TraceElement` carries one timestamp
   (start) and no duration. Reconstruct execution order by flattening every
   path's deque and sorting by timestamp; infer each step's end from the next
   step's start. Correct for `mode: single`, approximate under `parallel` and
   `repeat` where steps interleave. Synthesise span IDs deterministically —
   low 64 bits of a hash over `run_id + path + occurrence index` — so
   reprocessing is idempotent.

2. **One trace_id per run is wrong for nested runs.** An automation calling a
   script produces two `ActionTrace`s with two `run_id`s. `trace_set_child_id`
   records the linkage on the parent's step. Use the *root* run's ID as the
   trace_id for the whole tree; child `run_id`s become span attributes for
   cross-referencing back to the HA UI.

3. **`changed_variables` will leak.** It is a diff of the template variable
   namespace — full state dicts, secrets rendered into templates, presence data.
   Redaction must be designed in from the start, allowlist not denylist.

---

## 4. The activity / causality layer

### What HA gives you today

"Activity" is the logbook, renamed and rebuilt as a timeline in 2026.7. Still
`homeassistant.components.logbook` underneath. The "what caused this" column is
`ContextAugmenter.augment`: it takes an entry's `context_id`, looks up the row
that created that context, describes it, and **stops**. There is one conditional
hop to `CONTEXT_PARENT_ID_BIN_POS`, used only to avoid self-reference and to
attribute a `user_id` when the child context did not carry one. It never walks
the chain recursively.

So Activity answers "what caused this entry?" one level deep. It cannot trace
back through four automations to the person who opened the door, and cannot
answer the forward version — "this motion event fanned into 23 downstream
actions across 6 automations."

**This gap is the clearest justification for the project.** It is not
reimplementing Activity in Grafana; it is building the transitive closure
Activity deliberately does not compute.

### Context.id is a ULID

`Context.__init__` does `self.id = id or ulid_now()`. 128 bits, Crockford
base32, first 48 bits are a millisecond timestamp. Decode to 16 bytes and you
have a time-sortable trace ID carrying its own creation time.

This is a cleaner anchor than `run_id`. Runs get IDs from `run_id`, but the
*causal chain* spans many runs plus all the service calls and state changes
between them, which have no run at all. Context is the only identifier present
on every link.

### Design

- **trace_id** = root context of the chain (walk `parent_id` until null),
  ULID-decoded to 16 bytes.
- **spans** = each `EVENT_CALL_SERVICE` and `EVENT_STATE_CHANGED` carrying a
  context in that chain, plus every `ActionTrace` run whose `trigger_context` is
  in it.
- **parent span** = resolved via `Context.parent_id`; HA already threads this as
  `Context(parent_id=triggering_context.id)` in `AutomationEntity.async_trigger`.
- `run_id` and `context.id` as span attributes for deep-linking to the HA UI.
- Core runtime structure: a bounded `context_id → root trace_id` LRU. Logbook
  has the same shape in `LogbookRun.context_user_ids`, though at
  `MAX_CONTEXT_USER_IDS_CACHE = 256` it is sized for a different job.

`Context.origin_event` is set once, in-process, to the `Event` that first carried
the context (`core.py` ~L1387). Logbook uses it as a DB-free fallback for live
streams — same trick works here, full describing context on the hot path without
touching the recorder.

### Worked example

Someone unlocks the front door from the app → service call with
`Context(user_id=…)`, no parent. Root.
Lock state change inherits the context → `automation.arrive_home` triggers with
`Context(parent_id=<lock context>)`, a child span, its `run_id` giving the whole
`ActionTrace` step tree underneath → calls `script.evening_lights`, a nested run
linked by `TraceElement.child_id` → its `light.turn_on` produces service-call and
state-change spans → a second automation watching those lights fires with another
`parent_id` hop.

One trace, five levels, root-caused to a named person. Activity shows five
separate entries each with one hop.

### Hard problems

1. **Chain termination.** Physical feedback loops close the circle (automation →
   heater → temperature sensor → automation). The chain does not naturally end,
   and a Zigbee group update can fan one root into thousands of spans. Need a
   hard depth cap and a max-spans-per-trace budget, with overflow becoming span
   links rather than children.

2. **Traces stay open for a long time.** A script with `delay: 00:30:00` emits
   spans half an hour after the root. Do not buffer and assemble complete traces
   in the integration — emit each span as it happens with the correct trace ID
   and let the backend assemble. This is the one place OTLP's design genuinely
   saves you.

3. **Backfill is expensive.** The recorder persists `context_id_bin`,
   `context_parent_id_bin`, `context_user_id_bin` on both `events` and `states`,
   so historical reconstruction is possible. But HA's own comment in
   `logbook/processor.py` notes `context_parent_id_bin` is sparsely populated and
   scanning `States` for non-null parents costs ~40% of overall query time. On a
   19 GB SQLite recorder this is brutal. Separate offline tool, not the
   integration.

---

## 5. Signal coverage matrix

Three of four OTel signals are viable. **Profiles is not** — the OTLP profiles
signal is still in development, and HA's `profiler` integration is on-demand
cProfile and memory dumps, not continuous sampling.

| HA surface | Signal | Source | Effort |
|---|---|---|---|
| Python logging / `system_log` | Logs | root logger handler | solved by remote_logger |
| Event bus (state, service, registry) | Logs | bus subscription | solved by remote_logger |
| Entity states | Metrics | same conversion `prometheus` does | low, strong precedent |
| Integration setup | Traces + Metrics | `async_get_setup_timings()`, `SetupPhases` | low — public API |
| Assist pipeline runs | Traces | paired `PipelineEventType` start/end | low-medium |
| Automation / script runs | Traces | `ActionTrace.finished()` | medium, needs a hook |
| Context causality chains | Traces | event bus + ULID walk | high — the real work |
| Coordinator refresh | Metrics + Traces | wrap `DataUpdateCoordinator` | medium, monkeypatch |
| Recorder | Metrics | system health + SQLAlchemy | medium |
| Supervisor / apps | Metrics | `hassio` integration, `/addons/{slug}/stats` | low |
| HTTP + WebSocket API | Traces | aiohttp middleware | high — no public hook |
| Event loop lag, blocking calls | Metrics | none exposed | needs core |
| Continuous profiling | Profiles | — | not feasible |

Two easy wins worth noting:

- `homeassistant.setup` exposes `async_get_setup_timings()` and
  `async_get_domain_setup_times()` as public APIs with a `SetupPhases` enum.
  Startup spans are nearly free, and startup time is one of the most-wanted
  visibility gaps.
- **Assist pipeline is already span-shaped.** `PipelineEventType` emits paired
  `run-start`/`run-end`, `wake_word-start`/`-end`, `stt-start`/`-end`,
  `intent-start`/`intent-progress`/`-end`, `tts-start`/`-end`, plus `error`.
  Real start *and* end timestamps — better raw material than automation traces.
  Maps onto OTel GenAI semantic conventions. Separate, easier phase.

Also available almost free: **inbound W3C trace context propagation.** HA's REST
API and webhooks receive `traceparent` headers, so an external system's trace
could continue into HA rather than starting fresh. Design the context model to
allow it even if v1 doesn't build it.

---

## 6. Do not use the OpenTelemetry Python SDK

### It is thread-based, not async

Exporters expose only a blocking `export()`. Async variants are issue #3273 on
`opentelemetry-python`, open since April 2023, unresolved.

Batch processors work around this with threads. Current `_shared_internal`
module backing `BatchSpanProcessor` and `BatchLogRecordProcessor`:
`threading.Thread(daemon=True)` worker, `threading.Lock`, `threading.Event`.
`PeriodicExportingMetricReader` is the same shape. The HTTP exporter has moved
off `requests` onto `opentelemetry-exporter-http-transport[urllib3]` — still
blocking.

### That part is survivable

`span.end()` is a non-blocking enqueue onto a deque; export happens on the worker
thread, so the event loop is not blocked. Caveats:

- **Unmanaged threads.** HA expects background work via
  `hass.async_create_background_task` or the executor so shutdown is sequenced.
  Must call `provider.shutdown()` in `async_unload_entry`, from the executor
  (it joins the thread). Miss this and every config entry reload leaks a thread.
- **Import placement.** HA flags blocking imports in the event loop.
  `opentelemetry.sdk` is a large tree doing file I/O on import. Top-level module
  imports are fine (HA loads custom component modules in the executor); a lazy
  `import` inside `async_setup_entry` breaks.
- **Context API asyncio issues do not apply.** The usual `attach`/`detach`
  failures happen when spans cross await points in concurrent tasks. This design
  never uses ambient propagation — HA carries its own causality in
  `Context.id`/`parent_id`, and traces are reconstructed after the fact with
  explicit parent `SpanContext`s.

### The actual blocker: dependency constraints

HA installs custom integration requirements via
`uv pip install --constraint homeassistant/package_constraints.txt` —
`pip_kwargs()` in `requirements.py` sets it unconditionally, for custom
components too.

HA's pins are therefore forced onto your transitive tree:

```
protobuf==6.33.6
grpcio==1.78.0
requests==2.34.2
urllib3>=2.0
typing-extensions>=4.16.0,<5.0
```

`opentelemetry-exporter-otlp-proto-http` pulls `googleapis-common-protos ~= 1.52`,
`opentelemetry-proto`, `opentelemetry-exporter-http-transport[urllib3]` and their
own pins. Any resolution incompatible with exactly `protobuf==6.33.6` fails the
install and the integration never loads — on the user's machine, at setup time,
on an HA version never tested against.

Worse on HAOS: `pip_kwargs()` only sets a `target` directory outside a virtualenv
*and* outside Docker. HAOS runs HA in a container, so `is_docker_env()` is true
and deps install into the container's site-packages, which is replaced on every
HA update. The tree gets reinstalled each upgrade; a resolution failure means
silent load failure after a routine update.

### Decision: hand-roll, `"requirements": []`

Give up: samplers, batch processor, retry/backoff, protobuf encoding.

Get: export via `async_get_clientsession(hass)` (shared aiohttp session,
connection pooling, proxy handling); batching as an asyncio task under
`hass.async_create_background_task` so HA owns lifecycle and shutdown; zero
version risk across every HA release.

OTLP/JSON over HTTP is a fully supported wire encoding, not a fallback. JSON span
encoder ≈150 lines, batch-and-retry loop ≈150 more.

**Testing trick:** install the real SDK in the *test* environment, where pins are
free, and assert in CI that hand-rolled JSON matches SDK output for the same
spans. Spec conformance without shipping the dependency. Same for
`opentelemetry-semantic-conventions` — use it in tests to check attribute names,
copy the constants needed into your own module.

---

## 7. Core integration vs HACS

### Precedent is good

Core already ships seven observability exporters:

| Integration | config_flow | quality_scale | type |
|---|---|---|---|
| `splunk` | yes | **silver** | service |
| `datadog` | yes | — | service |
| `sentry` | yes | — | service |
| `influxdb` | yes | — | — |
| `prometheus` | no | legacy | — |
| `statsd` | no | legacy | — |
| `graphite` | no | legacy | — |

"Telemetry exporter" is an accepted category. `splunk` shows the modern shape:
config flow, `integration_type: service`, silver quality scale. `sentry` ships a
full vendor observability SDK as a requirement, so even the dependency objection
has precedent.

Note also: `protobuf` and `grpcio` are already pinned core constraints, so the
SDK would not introduce a new heavyweight tree in core — it would just have to
pin compatibly. (Still not worth it for a HACS component; see §6.)

### But start on HACS

The trace work needs a hook that does not exist. Logs, metrics and Assist
pipeline traces can all be built from public APIs and the event bus. Automation
and script traces cannot — `ActionTrace.finished()` has no callback and fires no
event, so a custom component must monkeypatch. Fragile across minor releases, and
exactly what core review rejects.

**Split the project:**

1. **A HACS integration** doing everything achievable from public surfaces, plus
   a clearly-isolated monkeypatch module for automation traces. Where you
   iterate, break things, and discover what the attribute schema should be.
   Realistically lives here for a long time.
2. **A small core PR** adding a trace-completion hook — a callback registry or an
   event fired in the `finally:` of `trace_automation` and `trace_script`.
   Narrow, defensible, useful to anyone building trace tooling, not coupled to
   OpenTelemetry at all. Land it and the integration drops its most fragile code.

Core submission of the exporter itself is a later question and should gate
nothing. It brings a codeowner commitment and review on every change — wrong
overhead for something whose semantic conventions will be revised for a year.
Signal to watch: HA's own team landing event-loop-lag metrics or setup-phase
instrumentation would suggest appetite.

**Fork or collaborate with `remote_logger` rather than greenfield.** It covers
logs well, already subscribes to the causality events, is a HACS default repo
(so already cleared review), and the author has clearly thought about batching,
loop prevention and diagnostic entities — the three things that bite every
exporter.

---

## 8. Naming

Three separate strings; only the domain is painful to change (config entry key,
`.storage/` filename, service names, entity IDs).

**Domain: `opentelemetry`.** Core's convention for this category is the
unabbreviated brand name (`prometheus`, `influxdb`, `datadog`, `sentry`,
`splunk`). Pick for where it might end up.

Checked collisions in core — **taken**: `trace` (the automation trace component
itself), `logger`, `system_log`, `thread` (Thread/Matter networking — avoid
entirely), `analytics` (HA's own opt-in telemetry to Nabu Casa).
**Free**: `otel`, `telemetry`, `observability`, `monitor`, `beacon`.

Avoid `telemetry` — users will read it as related to `analytics` and assume it
phones home to Nabu Casa. `otel` is tempting but breaks core convention and
invites a rename request later.

**Display name:** OpenTelemetry is a CNCF mark. remote_logger threads this well
with "Remote Logger for OpenTelemetry and Syslog" — distinct project name, then
a compatibility statement. Preferred: **"OpenTelemetry Exporter"**.
Alternatives: "Observability for OpenTelemetry", "Open Home Telemetry".

**Repo:** `home-assistant-opentelemetry`.

Caveat: if the causal-chain reconstruction becomes the product rather than the
protocol adapter, a real project name is worth it and the domain should match the
project, not the protocol. Deliberate branding decision, not a default.

---

## 9. Open questions

- Attribute schema — which OTel semantic conventions apply, what needs new ones.
- Span-emission model: event-bus subscription set, LRU resolution logic, where
  the depth/span-count caps go.
- Redaction policy for `changed_variables` and state attributes.
- Metric cardinality strategy — per-entity labels are the main design risk.
- Whether to fork remote_logger or contribute traces upstream to it.
- Collector deployment: local add-on vs off-box host.
- Target instance side note: recorder is SQLite at ~19 GB with run history only
  back to 30 July. Separate problem, but relevant if part of the motivation is
  long-term data retention outside HA.

---

## Key source references

| What | Where |
|---|---|
| `ActionTrace`, `run_id`, `set_trace`, `finished()` | `homeassistant/components/trace/models.py` |
| `TraceElement`, `trace_path`, `trace_set_child_id`, contextvars | `homeassistant/helpers/trace.py` |
| `trace_automation` context manager | `homeassistant/components/automation/trace.py` |
| `Context` (ULID id, parent_id, user_id, origin_event) | `homeassistant/core.py` |
| `ContextAugmenter`, one-hop resolution | `homeassistant/components/logbook/processor.py` |
| `async_get_setup_timings`, `SetupPhases` | `homeassistant/setup.py` |
| `pip_kwargs()`, constraint enforcement | `homeassistant/requirements.py` |
| `install_package(constraints=...)` | `homeassistant/util/package.py` |
| Core pins | `homeassistant/package_constraints.txt` |
| `PipelineEventType` | `homeassistant/components/assist_pipeline/pipeline.py` |
| Batch processor threading | `opentelemetry-sdk/src/opentelemetry/sdk/_shared_internal/__init__.py` |
| Async exporter request | `open-telemetry/opentelemetry-python` issue #3273 |
