# Manufacturing 101 for a Forward Deployed Engineer

## What it is

The "shop floor" is a layered stack of computers and controllers that run
physical machines and record what they do. As an FDE at a manufacturing-AI
company, you rarely touch the metal — but you constantly integrate with the
systems that sit on top of it. This file is the vocabulary and the physics you
need to speak credibly with a plant manager, a controls engineer, and a data
engineer in the same meeting.

The canonical mental model is the **automation pyramid** (roughly ISA-95 levels):

```
Level 4  ERP            business: orders, inventory, cost, scheduling (SAP, Oracle)
Level 3  MES            execution: work orders, quality, genealogy, OEE
Level 2  SCADA / HMI    supervision: operator screens, alarms, setpoints
Level 1  PLC / CNC      control: the real-time logic that moves the machine
Level 0  Sensors/actuators   the physical signals (temp, flow, vibration, motors)
```

Data and money flow *down* as commands/schedules and *up* as telemetry/results.
The higher you go, the slower and more aggregated the data; the lower you go, the
faster and noisier (millisecond control loops at Level 1, hourly KPIs at Level 4).

## Why it matters

An FDE's job is to land an AI product inside this stack without breaking it. Every
integration question — "where do we read the data?", "can we write a setpoint
back?", "why is this tag missing for 3 minutes?", "is that a real fault or a
bad sensor?" — is answered in these layers. If you can name the systems, the
protocols, and the failure physics, you sound like someone who has been on a
plant floor. If you can't, you sound like someone who has only seen a CSV.

The **Factory Incident Engine (FIE)** is a deliberately small, self-contained
model of exactly this world: it simulates a plant telemetry feed, ingests it the
way a real historian pipeline would, and reconstructs incidents. It is your
sandbox for talking about the whole stack.

## The systems an FDE meets

- **PLC (Programmable Logic Controller)** — a ruggedized industrial computer
  running a deterministic scan loop (read inputs → run ladder/structured-text
  logic → write outputs, every few ms). This is Level 1 control. Brands:
  Allen-Bradley (Rockwell), Siemens S7, Beckhoff. You almost never let AI write
  directly to a PLC — that path is safety-critical.
- **CNC (Computer Numerical Control)** — the controller for a machine tool
  (mill, lathe, grinder). It executes G-code to move axes and spin a spindle.
  FIE's fleet is CNC machines plus one press (`config.MACHINES = ["CNC-17",
  "CNC-18", "CNC-19", "PRESS-02"]`).
- **SCADA (Supervisory Control And Data Acquisition)** — the operator-facing
  supervision layer: HMI screens, alarms, trend charts, setpoint entry. It
  polls PLCs and presents state to humans.
- **MES (Manufacturing Execution System)** — Level 3. Tracks *work orders*,
  *quality events*, *genealogy* (which lot/tool made which part), downtime
  reasons, and OEE. In FIE, MES events are first-class evidence: startup,
  shutdown, config_change, error_code, order_start/complete (see
  `MesEvent.event` in `fie/models.py`).
- **ERP (Enterprise Resource Planning)** — Level 4 business system (SAP,
  Oracle). Orders, materials, cost, planning. FIE does not model ERP — a good
  scoping instinct: incident RCA lives at Levels 1–3.
- **Historian** — a time-series database purpose-built for plant tags (OSIsoft
  PI, AVEVA, Aspen IP.21, InfluxDB). It stores millions of tag samples with
  compression and fast time-range queries. FIE's SQLite `telemetry` table
  (`fie/store.py`, `fie/schema.sql`) is a tiny historian: `(machine, ts,
  signal, value)` rows queried by time window in `Store.query_readings`.
- **OPC-UA** — the modern vendor-neutral protocol for moving data between
  Levels 1–3. Structured address space, security, pub/sub. If someone asks "how
  would you get the data?", OPC-UA is the default answer for a greenfield line.
- **Modbus** — an old, dead-simple register-based serial/TCP protocol. Still
  everywhere on older equipment. No types, no security — just numbered
  registers. Contrast with OPC-UA to show you know the range.

### Telemetry, tags, and signals

A **tag** is a named point in the plant (e.g. `CNC17.Spindle.TempC`). A
**telemetry sample** is a `(tag, timestamp, value)` reading — plus quality/units
in richer systems. FIE calls a tag a **signal**, and a sample a
`TelemetryReading` (`fie/models.py`): `id, machine, ts, signal, value, source`.
The `id` is a deterministic hash used as an idempotency key so a re-delivered
sample is a harmless duplicate, not a double-count — exactly what a real
at-least-once historian feed needs.

## How THIS project models the plant

FIE's config is the single source of physical truth (`fie/config.py`):

| Signal (`SIGNAL_BOUNDS`) | Physical bound | Nominal (`NOMINAL`) | Real-world meaning |
|---|---|---|---|
| `spindle_temp_c` | 10–140 °C | 55 | Spindle bearing/motor temperature |
| `vibration_mm_s` | 0–45 mm/s RMS | 2.5 | Vibration velocity (ISO 10816 style) |
| `spindle_load_pct` | 0–100 % | 62 | Spindle motor load / torque demand |
| `coolant_flow_lpm` | 0–60 L/min | 28 | Coolant delivered to the cut |
| `throughput_pph` | 0–400 parts/hr | 180 | Production rate |
| `defect_rate_pct` | 0–100 % | 1.2 | Fraction of parts out of spec |

`SAMPLE_SECONDS = 60` means one frame per machine per minute — a realistic
"aggregated to the historian" rate, not the raw ms control loop. The bounds are
shared by *both* the simulator (to know when it is injecting an impossible value)
and the ingestion validator (to reject one). That single-source-of-truth design
is worth calling out in an interview: it is why the "impossible value" defect and
its rejection can never silently disagree.

Three data sources fuse into evidence (`EvidenceBundle` in `fie/models.py`):
telemetry (the numbers), maintenance records (`MaintenanceRecord`: inspection,
repair, replace, lubrication, calibration on a named `component`), and MES events.
Good RCA is exactly this fusion — a number rarely explains itself; the
maintenance note and the config-change event give it meaning.

## The failure physics — read `fie/simulator/scenarios.py`

The heart of the domain modeling is the scenario catalog. Each scenario deforms
signals over an incident window using one of four `Mode`s (defined at the top of
`scenarios.py`): `flat` (nominal + noise), `linear` (ramp to a target),
`step_at` (jump and hold — sensor-like), `spike_at` (brief excursion). The eight
failure modes are chosen so that the *easy-to-confuse pairs* are the whole point.
This is the FDE lesson: the hard part of factory AI is not detecting that
"something is hot" — it is telling apart four different reasons it might read hot.

1. **Cooling degradation** (`_cooling`) — coolant pump fails, so coolant flow
   *falls* and spindle temp *rises together*. Both ramp linearly (coolant → 6
   L/min, temp → 122 °C). The tell is **correlation**: temp and coolant move in
   lock-step. Backed by a prior "coolant pump flow marginal" inspection record
   and E-THERM MES codes.

2. **Sensor fault** (`_sensor_fault`) — the *trap*. Temp shows a `step_at` jump
   to 119 °C at minute 12, but **coolant flow and load stay flat/nominal**. There
   is no physical driver for real heat, so the temperature reading itself is
   wrong. Physics rule: a genuine thermal event needs an energy-balance cause
   (less cooling *or* more load). Its absence ⇒ instrument fault, not overheating.
   This is the case the buggy engine gets wrong (see below).

3. **Overload** (`_overload`) — operator overrides feed rate +25% (a
   `config_change` MES event), spindle `load_pct` ramps to ~99% and *drives* temp
   to 110 °C, while **coolant stays nominal**. Same symptom as cooling (hot
   spindle) but the driver is pinned load, not lost cooling.

4. **Bearing wear** (`_bearing`) — mechanical degradation: vibration ramps to 18
   mm/s, throughput sags, temp rises modestly. The signature is *rising
   vibration*, terminating in an E-VIB threshold trip. Physically this is a
   worn/spalled bearing raising mechanical friction and imbalance.

5. **Tool wear** (`_tool_wear`) — the cutting tool dulls over 90 minutes, so
   **defect rate climbs** (to 11%) and throughput drops, with temp and vibration
   flat. Gradual quality drift, not a mechanical fault.

6. **Operator config** (`_operator_config`) — a `config_change` (RPM profile
   change) is *immediately followed* by a step rise in defect rate and vibration.
   The signature is temporal: degradation begins right after the change. Easy to
   confuse with tool wear (both raise defect rate) — the discriminator is the
   config event.

7. **No incident** (`_no_incident`) — the control case. All signals nominal. A
   good engine must **not invent** a root cause.

8. **Insufficient** (`_insufficient`) — a telemetry outage (`gap_min=(5, 36)`)
   blanks 72% of the window. The engine must **abstain** ("unknown / blocked"),
   not guess. This models the reliability gate (`fie/reliability.py`).

`catalog()` instantiates each of the eight builders on rotating assets (twice
each) → **16 golden incidents**. The golden JSON lives in `data/golden/`.

## Mental model

> **Symptoms are shared; causes are separated by corroboration.** A hot spindle
> can mean lost cooling, too much load, a lying sensor, or a worn bearing. You
> only tell them apart by asking "what *else* moved?" — coolant flow, load,
> vibration, and the maintenance/MES record. The plant stack exists to give you
> those corroborating signals; the FDE's job is to fuse them.

Diagnosis is an **energy/physics balance plus a timeline**: if temperature rose,
*something* must have added heat or removed cooling — find that driver, or
distrust the reading.

## Interview Q&A

**Q: Walk me through the systems between a sensor and a business dashboard.**
Sensor (L0) → PLC/CNC real-time control (L1) → SCADA supervision + alarms (L2) →
MES work orders/quality/OEE (L3) → ERP orders/cost (L4). Fast and raw at the
bottom, slow and aggregated at the top. Telemetry flows up; commands/schedules
flow down. A historian sits alongside L2/L3 to store the time-series.

**Q: How would you get data out of a line for an AI product?**
Prefer OPC-UA for a modern line — typed address space, security, pub/sub. Fall
back to a historian API (PI Web API, etc.) for stored tags, or Modbus TCP for
legacy gear that only exposes registers. I'd read from the historian/OPC layer,
never write into a PLC directly — that path is safety-critical and owned by
controls engineers.

**Q: What's a tag? What's telemetry?**
A tag is a named plant point (`CNC17.Spindle.TempC`); telemetry is its stream of
`(timestamp, value)` samples, usually with a quality flag and units. In FIE a tag
is a `signal` and a sample is a `TelemetryReading` with a deterministic `id` for
idempotency.

**Q: A spindle is reading 120 °C. Is that an emergency?**
Not by itself — I need corroboration. If coolant flow collapsed in lock-step,
it's a real cooling failure. If load is pinned near 100%, it's an overload. If
coolant and load are both nominal, the *sensor* is probably lying — a real
thermal event needs a driver. That branch is exactly FIE's cooling vs overload vs
sensor_fault split in `scenarios.py` and the v1.2 classifier in
`fie/agent/engine.py`.

**Q: How do you distinguish tool wear from an operator config change? Both raise
defect rate.** By the timeline. Tool wear is a *gradual* ramp with no triggering
event; operator config shows a *step* in defect/vibration that starts right after
a `config_change` MES event. FIE encodes this: `_tool_wear` uses `linear`,
`_operator_config` uses `step_at` at minute 10 right after a minute-9 config
event.

**Q: Why model only CNC machines and one press instead of a whole plant?**
Depth over breadth. One asset class modeled with real failure physics and
confusable pairs is far more useful — and more honest — than a shallow model of
fifty machine types. It also mirrors how FDE pilots actually start: one line, one
asset class, done properly.

**Q: What is MES data good for in RCA?** Context and timeline anchors. Error
codes and shutdowns mark *when* the process reacted; config changes mark operator
*interventions*; order start/complete bound the run. FIE folds MES events into
the incident timeline and uses `config_change` as the discriminator between
operator-caused and wear-caused degradation.

## Resources

- ISA-95 / IEC 62264 — the enterprise-control integration standard that defines
  the L0–L4 hierarchy and MES scope.
- ISA-88 — batch control (adjacent, for process/batch plants).
- OPC Foundation — OPC-UA specification and reference stack (`opcua`).
- Modbus specification — Modbus Organization (`modbus.org`).
- OSIsoft/AVEVA PI System documentation — the reference historian.
- ISO 10816 / ISO 20816 — mechanical vibration evaluation of machines (the basis
  for vibration thresholds like FIE's `vibration_mm_s`).
- "The Toyota Way" (Liker) and lean-manufacturing OEE material — for the
  business vocabulary (OEE, availability, quality, throughput) an FDE hears daily.
- In-repo: `fie/config.py` (signals/bounds/nominal), `fie/simulator/scenarios.py`
  (failure physics), `docs/failure-model.md` (the confusable-pairs table).
</content>
</invoke>
