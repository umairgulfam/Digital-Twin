"""Thermal and electrical model of a server room.

Deliberately small and readable rather than CFD-accurate. Every number that
comes out of here is defensible from a first-principles energy balance, which
is what makes the what-if engine in phase 6 worth trusting.

The same class backs both the live simulator and the scenario sandbox, so a
what-if run and a live run cannot silently diverge.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

AIR_CP = 1.006          # kJ/(kg*K)
AIR_DENSITY = 1.2       # kg/m3 at room conditions
CFM_TO_M3S = 0.000471947


def mass_flow_kg_s(cfm: float) -> float:
    return cfm * CFM_TO_M3S * AIR_DENSITY


def thermal_capacity_kw_per_k(cfm: float) -> float:
    """kW of heat carried per kelvin of temperature rise at this airflow."""
    return mass_flow_kg_s(cfm) * AIR_CP


@dataclass
class Server:
    id: str
    hostname: str
    rated_power_kw: float
    u_size: int
    base_util_pct: float
    util_pct: float = 0.0
    throttling: bool = False

    def tick(self, rng: random.Random, load_multiplier: float = 1.0) -> None:
        drift = rng.gauss(0, 4.0)
        target = self.base_util_pct * load_multiplier + drift
        self.util_pct = max(2.0, min(100.0, target))

    @property
    def power_kw(self) -> float:
        # Idle draw is ~40% of rated; the rest scales with utilisation.
        idle = 0.4 * self.rated_power_kw
        return idle + 0.6 * self.rated_power_kw * (self.util_pct / 100.0)


@dataclass
class Rack:
    id: str
    aisle: str
    position_x: float
    position_y: float
    u_height: int
    max_inlet_temp_c: float
    shutdown_temp_c: float
    airflow_cfm: float
    recirculation_factor: float
    pdu_id: str
    pdu_capacity_kw: float
    sensors: list[dict[str, str]]
    servers: list[Server]
    inlet_temp_c: float = 22.0
    outlet_temp_c: float = 30.0

    @property
    def power_kw(self) -> float:
        return sum(s.power_kw for s in self.servers)

    def update_temps(
        self, supply_temp_c: float, ambient_temp_c: float, airflow_ratio: float = 1.0
    ) -> None:
        """Inlet air is supply air polluted by hot-aisle recirculation.

        Recirculation is not a fixed property of the rack — it depends on how
        much cold air is actually being delivered. Lose half the CRAC airflow
        and the cold aisle stops being distinct from the room; lose all of it
        and the rack simply breathes ambient air.
        """
        base = self.recirculation_factor
        recirc = base + (1.0 - max(0.0, min(1.0, airflow_ratio))) * (1.0 - base)
        self.inlet_temp_c = supply_temp_c + recirc * max(0.0, ambient_temp_c - supply_temp_c)

        capacity = thermal_capacity_kw_per_k(self.airflow_cfm)
        delta_t = self.power_kw / capacity if capacity > 0 else 0.0
        self.outlet_temp_c = self.inlet_temp_c + delta_t

        # Servers throttle once intake air passes the ASHRAE recommended limit.
        for s in self.servers:
            s.throttling = self.inlet_temp_c > self.max_inlet_temp_c

    @property
    def delta_t_c(self) -> float:
        return self.outlet_temp_c - self.inlet_temp_c

    @property
    def thermal_margin_c(self) -> float:
        return self.shutdown_temp_c - self.inlet_temp_c

    @property
    def ashrae_compliant(self) -> bool:
        return self.inlet_temp_c <= self.max_inlet_temp_c


@dataclass
class CracUnit:
    id: str
    rated_cooling_kw: float
    setpoint_c: float
    airflow_cfm: float
    power_kw_at_full: float
    state: str = "running"          # running | standby | failed
    supply_temp_c: float = 18.0
    return_temp_c: float = 26.0
    cooling_delivered_kw: float = 0.0

    def cool(self, ambient_temp_c: float) -> float:
        """Return heat removed this tick, in kW."""
        self.return_temp_c = ambient_temp_c
        if self.state != "running":
            self.cooling_delivered_kw = 0.0
            # A dead unit's coil drifts toward room temperature.
            self.supply_temp_c += (ambient_temp_c - self.supply_temp_c) * 0.08
            return 0.0

        capacity = thermal_capacity_kw_per_k(self.airflow_cfm)
        # A unit can only pull the air down by rated_cooling / airflow capacity.
        # Past that it is saturated and supply temperature drifts above setpoint.
        max_drop_k = self.rated_cooling_kw / capacity if capacity else 0.0
        self.supply_temp_c = max(self.setpoint_c, self.return_temp_c - max_drop_k)
        self.cooling_delivered_kw = capacity * max(
            0.0, self.return_temp_c - self.supply_temp_c
        )
        return self.cooling_delivered_kw

    @property
    def power_kw(self) -> float:
        if self.state != "running":
            return 0.0
        load_fraction = (
            self.cooling_delivered_kw / self.rated_cooling_kw
            if self.rated_cooling_kw
            else 0.0
        )
        # Fans run regardless; compressor scales with load.
        return self.power_kw_at_full * (0.35 + 0.65 * load_fraction)


@dataclass
class Ups:
    id: str
    capacity_kwh: float
    rated_power_kw: float
    battery_pct: float = 100.0
    on_battery: bool = False
    load_kw: float = 0.0

    def tick(self, load_kw: float, dt_s: float) -> None:
        self.load_kw = load_kw
        if self.on_battery:
            drawn_kwh = load_kw * (dt_s / 3600.0)
            pct_drop = (drawn_kwh / self.capacity_kwh) * 100.0 if self.capacity_kwh else 0.0
            self.battery_pct = max(0.0, self.battery_pct - pct_drop)
        elif self.battery_pct < 100.0:
            self.battery_pct = min(100.0, self.battery_pct + (dt_s / 3600.0) * 20.0)

    @property
    def runtime_remaining_min(self) -> float:
        if self.load_kw <= 0:
            return math.inf
        stored_kwh = self.capacity_kwh * (self.battery_pct / 100.0)
        return (stored_kwh / self.load_kw) * 60.0


class RoomModel:
    """Integrates the room energy balance one timestep at a time."""

    def __init__(self, config: dict[str, Any], seed: int | None = 42):
        rc = config["room"]
        self.room_id: str = rc["id"]
        self.display_name: str = rc["displayName"]
        self.floor_area_m2: float = rc["floorAreaM2"]
        self.design_capacity_kw: float = rc["designCapacityKw"]
        self.setpoint_c: float = rc["setpointC"]
        self.ashrae_class: str = rc["ashraeClass"]
        self.thermal_mass_kj_per_k: float = rc["thermalMassKjPerK"]
        self.ambient_temp_c: float = rc["ambientStartC"]
        self.humidity_pct: float = rc["humidityPct"]
        self.door_open: bool = False

        self.rng = random.Random(seed)
        self.elapsed_s: float = 0.0

        self.racks: list[Rack] = [
            Rack(
                id=r["id"],
                aisle=r["aisle"],
                position_x=r["positionX"],
                position_y=r["positionY"],
                u_height=r["uHeight"],
                max_inlet_temp_c=r["maxInletTempC"],
                shutdown_temp_c=r["shutdownTempC"],
                airflow_cfm=r["airflowCfm"],
                recirculation_factor=r["recirculationFactor"],
                pdu_id=r["pdu"]["id"],
                pdu_capacity_kw=r["pdu"]["capacityKw"],
                sensors=r["sensors"],
                servers=[
                    Server(
                        id=s["id"],
                        hostname=s["hostname"],
                        rated_power_kw=s["ratedPowerKw"],
                        u_size=s["uSize"],
                        base_util_pct=s["baseUtilPct"],
                    )
                    for s in r["servers"]
                ],
            )
            for r in config["racks"]
        ]

        self.crac: list[CracUnit] = [
            CracUnit(
                id=c["id"],
                rated_cooling_kw=c["ratedCoolingKw"],
                setpoint_c=c["setpointC"],
                airflow_cfm=c["airflowCfm"],
                power_kw_at_full=c["powerKwAtFull"],
            )
            for c in config["crac"]
        ]

        uc = config["ups"]
        self.ups = Ups(
            id=uc["id"],
            capacity_kwh=uc["capacityKwh"],
            rated_power_kw=uc["ratedPowerKw"],
        )

        self.load_multiplier: float = 1.0

    # --- scenario controls -------------------------------------------------

    def fail_crac(self, unit_id: str) -> None:
        for c in self.crac:
            if c.id == unit_id:
                c.state = "failed"

    def restore_crac(self, unit_id: str) -> None:
        for c in self.crac:
            if c.id == unit_id:
                c.state = "running"

    def set_setpoint(self, temp_c: float) -> None:
        for c in self.crac:
            c.setpoint_c = temp_c
        self.setpoint_c = temp_c

    def cut_utility(self) -> None:
        self.ups.on_battery = True

    # --- integration -------------------------------------------------------

    @property
    def it_load_kw(self) -> float:
        return sum(r.power_kw for r in self.racks)

    @property
    def cooling_power_kw(self) -> float:
        return sum(c.power_kw for c in self.crac)

    @property
    def facility_load_kw(self) -> float:
        return self.it_load_kw + self.cooling_power_kw

    @property
    def pue(self) -> float:
        it = self.it_load_kw
        return self.facility_load_kw / it if it > 0 else 0.0

    def step(self, dt_s: float = 5.0) -> None:
        self.elapsed_s += dt_s

        for rack in self.racks:
            for server in rack.servers:
                server.tick(self.rng, self.load_multiplier)

        heat_in_kw = self.it_load_kw
        if self.door_open:
            heat_in_kw += 1.5

        heat_out_kw = sum(c.cool(self.ambient_temp_c) for c in self.crac)

        net_kj = (heat_in_kw - heat_out_kw) * dt_s
        self.ambient_temp_c += net_kj / self.thermal_mass_kj_per_k

        running = [c for c in self.crac if c.state == "running"]
        supply_temp = (
            sum(c.supply_temp_c for c in running) / len(running)
            if running
            else self.ambient_temp_c
        )

        design_cfm = sum(c.airflow_cfm for c in self.crac)
        running_cfm = sum(c.airflow_cfm for c in running)
        airflow_ratio = running_cfm / design_cfm if design_cfm else 0.0

        for rack in self.racks:
            rack.update_temps(supply_temp, self.ambient_temp_c, airflow_ratio)

        self.ups.tick(self.facility_load_kw, dt_s)
        self.humidity_pct += self.rng.gauss(0, 0.15)
        self.humidity_pct = max(20.0, min(70.0, self.humidity_pct))

    # --- reporting ---------------------------------------------------------

    def hottest_rack(self) -> Rack:
        return max(self.racks, key=lambda r: r.inlet_temp_c)

    def cooling_headroom_kw(self) -> float:
        capacity = sum(c.rated_cooling_kw for c in self.crac if c.state == "running")
        return capacity - self.it_load_kw

    def status(self) -> str:
        margins = [r.thermal_margin_c for r in self.racks]
        if not margins:
            return "nominal"
        worst = min(margins)
        if worst <= 2.0:
            return "critical"
        if worst <= 6.0 or not all(r.ashrae_compliant for r in self.racks):
            return "warning"
        return "nominal"
