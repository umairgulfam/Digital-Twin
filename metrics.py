"""Turns raw model state into the telemetry payloads and derived twin properties.

Keeping this separate from model.py means the Azure Function in phase 4 can
import exactly the same derivation logic that the local ingest path uses.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from model import RoomModel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot(room: RoomModel) -> dict[str, Any]:
    """One full reading of the room: telemetry plus everything derived from it."""
    ranked = sorted(room.racks, key=lambda r: r.inlet_temp_c, reverse=True)
    rank_by_id = {r.id: i + 1 for i, r in enumerate(ranked)}

    racks = []
    for rack in room.racks:
        racks.append(
            {
                "id": rack.id,
                "telemetry": {
                    "inletTempC": round(rack.inlet_temp_c, 2),
                    "outletTempC": round(rack.outlet_temp_c, 2),
                    "humidityPct": round(room.humidity_pct, 1),
                    "powerKw": round(rack.power_kw, 3),
                },
                "derived": {
                    "currentInletTempC": round(rack.inlet_temp_c, 2),
                    "currentOutletTempC": round(rack.outlet_temp_c, 2),
                    "currentPowerKw": round(rack.power_kw, 3),
                    "deltaTC": round(rack.delta_t_c, 2),
                    "thermalMarginC": round(rack.thermal_margin_c, 2),
                    "hotspotRank": rank_by_id[rack.id],
                    "ashraeCompliant": rack.ashrae_compliant,
                },
                "pdu": {
                    "id": rack.pdu_id,
                    "telemetry": {
                        "powerKw": round(rack.power_kw, 3),
                        "currentA": round(rack.power_kw * 1000 / 230.0, 2),
                    },
                    "derived": {
                        "currentPowerKw": round(rack.power_kw, 3),
                        "utilisationPct": round(
                            100.0 * rack.power_kw / rack.pdu_capacity_kw, 1
                        )
                        if rack.pdu_capacity_kw
                        else 0.0,
                    },
                },
                "sensors": [
                    {
                        "id": s["id"],
                        "telemetry": {
                            "temperatureC": round(
                                rack.inlet_temp_c
                                if s["placement"] == "inlet"
                                else rack.outlet_temp_c,
                                2,
                            ),
                            "humidityPct": round(room.humidity_pct, 1),
                        },
                    }
                    for s in rack.sensors
                ],
                "servers": [
                    {
                        "id": s.id,
                        "telemetry": {
                            "cpuUtilPct": round(s.util_pct, 1),
                            "powerKw": round(s.power_kw, 3),
                            "intakeTempC": round(rack.inlet_temp_c, 2),
                        },
                        "derived": {
                            "currentCpuUtilPct": round(s.util_pct, 1),
                            "currentPowerKw": round(s.power_kw, 3),
                            "throttling": s.throttling,
                        },
                    }
                    for s in rack.servers
                ],
            }
        )

    crac = [
        {
            "id": c.id,
            "telemetry": {
                "supplyTempC": round(c.supply_temp_c, 2),
                "returnTempC": round(c.return_temp_c, 2),
                "powerKw": round(c.power_kw, 3),
            },
            "derived": {
                "currentSupplyTempC": round(c.supply_temp_c, 2),
                "coolingDeliveredKw": round(c.cooling_delivered_kw, 2),
                "operatingState": c.state,
            },
        }
        for c in room.crac
    ]

    runtime = room.ups.runtime_remaining_min
    ups = {
        "id": room.ups.id,
        "telemetry": {
            "loadKw": round(room.ups.load_kw, 3),
            "batteryPct": round(room.ups.battery_pct, 1),
            "onBattery": room.ups.on_battery,
        },
        "derived": {
            "currentLoadKw": round(room.ups.load_kw, 3),
            "currentBatteryPct": round(room.ups.battery_pct, 1),
            "runtimeRemainingMin": -1.0 if math.isinf(runtime) else round(runtime, 1),
        },
    }

    return {
        "timestamp": _now(),
        "elapsedS": round(room.elapsed_s, 1),
        "room": {
            "id": room.room_id,
            "telemetry": {
                "ambientTempC": round(room.ambient_temp_c, 2),
                "ambientHumidityPct": round(room.humidity_pct, 1),
                "doorOpen": room.door_open,
            },
            "derived": {
                "currentAmbientTempC": round(room.ambient_temp_c, 2),
                "itLoadKw": round(room.it_load_kw, 3),
                "facilityLoadKw": round(room.facility_load_kw, 3),
                "pue": round(room.pue, 3),
                "coolingHeadroomKw": round(room.cooling_headroom_kw(), 2),
                "hottestRackId": room.hottest_rack().id,
                "status": room.status(),
            },
        },
        "racks": racks,
        "crac": crac,
        "ups": ups,
    }


def summarise(snap: dict[str, Any]) -> str:
    """One-line console summary, for watching a run scroll past."""
    r = snap["room"]["derived"]
    hottest = max(snap["racks"], key=lambda x: x["derived"]["currentInletTempC"])
    return (
        f"t={snap['elapsedS']:>7.0f}s  "
        f"ambient={r['currentAmbientTempC']:5.2f}C  "
        f"IT={r['itLoadKw']:5.2f}kW  "
        f"PUE={r['pue']:.2f}  "
        f"hottest={hottest['id']} "
        f"inlet={hottest['derived']['currentInletTempC']:5.2f}C "
        f"margin={hottest['derived']['thermalMarginC']:5.2f}K  "
        f"[{r['status']}]"
    )
