# server-room-twin

A live digital twin of a server room on Azure Digital Twins.

A live, queryable twin of a server room. Sensor readings flow into an Azure
Digital Twins graph, derived metrics are computed on the way in, and the same
thermal model that drives the simulation also backs a what-if engine.

This is not a dashboard with a database behind it. The room is a graph of
twins with real relationships, and the interesting questions are graph
queries against live state.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | DTDL models and graph seeding | done |
| 2 | Physics-based simulator | done |
| 3 | Local ingest into the twin graph | done |
| 4 | Derived metrics + Event Grid alert rules | next |
| 5 | History in Azure Data Explorer, 3D Scenes Studio | planned |
| 6 | What-if engine on a forked graph | planned |

## Layout

```
server-room-twin/
├── models/           DTDL v3 interfaces — the heart of the project
├── config/room.yaml  single source of truth for the room layout
├── simulator/        thermal model, metric derivation, CLI
├── graph/            model upload, twin seeding, local ingest
└── README.md
```

`config/room.yaml` drives both seeding and simulation. Add a rack there and it
appears in the graph and in the physics on the next run — the two can't drift.

## The twin graph

```
server-room-a  (ServerRoom)
├── contains ──> rack-01..04  (Rack)
│                ├── contains ──> srv-*      (Server)
│                ├── contains ──> temp-*     (TemperatureSensor)
│                └── poweredBy ─> pdu-*      (PDU)
├── cooledBy ──> crac-01, crac-02  (CRACUnit)
└── backedBy ──> ups-01  (UPS)
```

Every asset extends a shared `Asset` interface, so `contains` can target one
type and still accept racks, servers, sensors and PDUs.

## Measured vs derived

Telemetry is what a sensor reports. Properties are what the twin knows.
The split matters — the derived half is what makes this a twin rather than a
feed.

| Twin | Measured | Derived |
|------|----------|---------|
| Rack | inlet/outlet temp, humidity, power | delta-T, thermal margin, hotspot rank, ASHRAE compliance |
| Server | CPU util, power, intake temp | throttling |
| CRAC | supply/return temp, power | cooling delivered, operating state |
| UPS | load, battery % | runtime remaining |
| Room | ambient temp/humidity, door | IT load, facility load, PUE, cooling headroom, hottest rack, status |

## Safety note

The committed `config/room.yaml` describes a fictional room. Keep it that way.

A real room's layout — rack positions, hostnames, power capacity, cooling
headroom, thermal margins — is a reconnaissance package. It tells someone which
rack to attack and how long the room survives without cooling. If you point
this at a real facility, put that config in `config/room.local.yaml`, which is
gitignored, and pass it with `--config`.

See [PUBLISHING.md](PUBLISHING.md) for the full checklist and the do's and
don'ts before making a repo public.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your instance URL
```

Create the Azure resources and grant yourself write access to the graph:

```bash
az dt create -n roomtwin-adt -g roomtwin-rg -l westeurope
az dt role-assignment create -n roomtwin-adt \
    --assignee "<your-user-principal>" --role "Azure Digital Twins Data Owner"
az login   # DefaultAzureCredential picks this up
```

## Running it

No Azure at all — just watch the physics:

```bash
cd simulator
python simulate.py --duration 600
python simulate.py --json --duration 60      # raw JSON lines
```

Seed the graph, then feed it live:

```bash
cd graph && python seed.py
cd ../simulator && python simulate.py --sink adt --interval 5
```

Through IoT Hub instead (phase 4 path):

```bash
python simulate.py --sink iothub --interval 5
```

## Scenarios

`--speed` compresses time: `--speed 60` runs an hour of room time per minute.
`--at` chooses when the scenario fires.

```bash
# lose one CRAC — does N+1 actually hold?
python simulate.py --scenario crac-failure --at 60 --duration 3600 --speed 0

# lose both — how long until the racks are in trouble?
python simulate.py --scenario crac-failure --unit crac-01,crac-02 \
    --at 60 --duration 7200 --speed 0

# raise the setpoint 2C to save on cooling — what does it cost in margin?
python simulate.py --scenario setpoint-raise --setpoint 20 --duration 3600 --speed 0

# training job lands on the GPU racks
python simulate.py --scenario load-spike --multiplier 1.6 --duration 1800 --speed 0

# utility cut, running on battery
python simulate.py --scenario utility-outage --duration 1800 --speed 0
```

## Sample output

Normal operation, roughly 12 kW of IT load. The room settles just under
setpoint and stays there:

```
t=     60s  ambient=21.97C  IT=11.79kW  PUE=1.69  hottest=rack-03 inlet=19.35C margin=15.65K  [nominal]
t=    300s  ambient=21.86C  IT=11.89kW  PUE=1.68  hottest=rack-03 inlet=19.31C margin=15.69K  [nominal]
t=    900s  ambient=21.69C  IT=12.10kW  PUE=1.65  hottest=rack-03 inlet=19.25C margin=15.75K  [nominal]
```

Lose one CRAC at t=60s. Redundancy holds — the room stabilises 2.5 K warmer and
stays inside the ASHRAE envelope, but thermal margin drops by 3 K:

```
t=    600s  ambient=22.67C  IT=11.80kW  PUE=1.37  hottest=rack-03 inlet=21.13C margin=13.87K  [nominal]
t=   1800s  ambient=23.69C  IT=11.96kW  PUE=1.40  hottest=rack-03 inlet=21.81C margin=13.19K  [nominal]
t=   3600s  ambient=24.42C  IT=11.93kW  PUE=1.43  hottest=rack-03 inlet=22.30C margin=12.70K  [nominal]
```

Lose both. Cooling airflow goes to zero, so recirculation goes to one and every
rack breathes ambient air. The room climbs about 12 K per hour:

```
t=    600s  ambient=23.80C  IT=11.80kW  PUE=1.00  hottest=rack-01 inlet=23.80C margin=11.20K  [nominal]
t=   1200s  ambient=25.84C  IT=11.94kW  PUE=1.00  hottest=rack-01 inlet=25.84C margin= 9.16K  [nominal]
t=   2400s  ambient=29.90C  IT=12.01kW  PUE=1.00  hottest=rack-01 inlet=29.90C margin= 5.10K  [warning]
t=   3600s  ambient=33.95C  IT=11.93kW  PUE=1.00  hottest=rack-01 inlet=33.95C margin= 1.05K  [critical]
t=   4500s  ambient=36.98C  IT=11.63kW  PUE=1.00  hottest=rack-01 inlet=36.98C margin=-1.98K  [critical]
```

Warning at 40 minutes, critical at 60, ASHRAE breach shortly after. That is the
answer to the only question that matters during an outage: you have about an
hour. It falls out of the energy balance — nothing about it is hardcoded.

Note the PUE reading of 1.00 in that last run: with no cooling drawing power,
facility load equals IT load. The metric is behaving correctly and telling you
something alarming.

## Queries to try

In the Azure Digital Twins Explorer query pane:

```sql
-- every rack outside the ASHRAE envelope
SELECT * FROM DIGITALTWINS T
WHERE IS_OF_MODEL(T, 'dtmi:roomtwin:Rack;1') AND T.ashraeCompliant = false

-- racks with less than 5 K of thermal margin
SELECT T.$dtId, T.currentInletTempC, T.thermalMarginC
FROM DIGITALTWINS T
WHERE IS_OF_MODEL(T, 'dtmi:roomtwin:Rack;1') AND T.thermalMarginC < 5

-- which racks does this room contain, and what are they drawing
SELECT rack.$dtId, rack.currentPowerKw
FROM DIGITALTWINS room
JOIN rack RELATED room.contains
WHERE room.$dtId = 'server-room-a' AND IS_OF_MODEL(rack, 'dtmi:roomtwin:Rack;1')

-- servers throttling because of intake air, and the rack they sit in
SELECT rack.$dtId, srv.hostname
FROM DIGITALTWINS rack
JOIN srv RELATED rack.contains
WHERE srv.throttling = true

-- cooling units not currently running
SELECT * FROM DIGITALTWINS T
WHERE IS_OF_MODEL(T, 'dtmi:roomtwin:CRACUnit;1') AND T.operatingState != 'running'
```

## Model notes

The thermal model is a lumped energy balance, not CFD. It is small enough to
read in one sitting and every term is defensible:

- Airflow converts to heat capacity as `cfm × 0.000472 × 1.2 × 1.006` kW/K
- Rack delta-T is `power / capacity` — the standard airflow relation
- Rack inlet is supply air plus recirculation from the hot aisle, and the
  recirculation fraction rises as CRAC airflow is lost. At zero airflow the
  rack simply breathes ambient air
- A CRAC can only pull air down by `rated / capacity` kelvin. Past that it is
  saturated and supply temperature drifts above setpoint
- Room ambient integrates `(heat in − heat out) × dt / thermal mass`

`simulator/model.py` is deliberately importable on its own. Phase 6 forks the
twin graph into a sandbox `RoomModel`, runs it forward under a hypothetical,
and reports the projected curve — same code, different starting state.

## License

MIT — see [LICENSE](LICENSE).

## Teardown

```bash
cd graph && python seed.py --delete
az group delete -n roomtwin-rg
```
