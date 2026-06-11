"""Writes a simulator snapshot into the twin graph.

This is the local stand-in for the phase 4 Azure Function. The patch-building
logic here is exactly what the Function will use once IoT Hub is in the loop,
so moving to the cloud path is a change of trigger, not a change of logic.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.digitaltwins.core import DigitalTwinsClient
from azure.identity import DefaultAzureCredential


def _patch(props: dict[str, Any]) -> list[dict[str, Any]]:
    """ADT accepts JSON Patch; 'replace' upserts for properties already in the model."""
    ops = [{"op": "replace", "path": f"/{k}", "value": v} for k, v in props.items()]
    ops.append(
        {"op": "replace", "path": "/lastSeen", "value": datetime.now(timezone.utc).isoformat()}
    )
    return ops


class TwinPusher:
    def __init__(self, url: str | None = None) -> None:
        url = url or os.environ.get("AZURE_DIGITALTWINS_URL")
        if not url:
            sys.exit("AZURE_DIGITALTWINS_URL is not set. See .env.example.")
        self.client = DigitalTwinsClient(url, DefaultAzureCredential())
        self.missing: set[str] = set()

    def _update(self, twin_id: str, props: dict[str, Any]) -> None:
        try:
            self.client.update_digital_twin(twin_id, _patch(props))
        except ResourceNotFoundError:
            if twin_id not in self.missing:
                self.missing.add(twin_id)
                print(f"! twin {twin_id} not found — run seed.py first", file=sys.stderr)

    def push(self, snap: dict[str, Any]) -> None:
        room = snap["room"]
        self._update(room["id"], room["derived"])

        for rack in snap["racks"]:
            self._update(rack["id"], rack["derived"])
            self._update(rack["pdu"]["id"], rack["pdu"]["derived"])
            for server in rack["servers"]:
                self._update(server["id"], server["derived"])
            for sensor in rack["sensors"]:
                self._update(
                    sensor["id"],
                    {
                        "lastReadingC": sensor["telemetry"]["temperatureC"],
                        "lastHumidityPct": sensor["telemetry"]["humidityPct"],
                    },
                )

        for unit in snap["crac"]:
            self._update(unit["id"], unit["derived"])

        self._update(snap["ups"]["id"], snap["ups"]["derived"])
