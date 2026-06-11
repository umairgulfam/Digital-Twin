#!/usr/bin/env python3
"""Server room simulator.

Runs the thermal model forward and emits readings to one of three sinks:

  --sink stdout   print JSON lines (no Azure needed at all)
  --sink iothub   send device-to-cloud messages to Azure IoT Hub
  --sink adt      patch the Azure Digital Twins graph directly (local ingest)

Examples
--------
  python simulate.py --sink stdout --duration 300
  python simulate.py --sink stdout --scenario crac-failure --at 120 --speed 60
  python simulate.py --sink adt --interval 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "graph"))

from metrics import snapshot, summarise  # noqa: E402
from model import RoomModel  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "room.yaml"

SCENARIOS = ("normal", "crac-failure", "load-spike", "utility-outage", "setpoint-raise")


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def apply_scenario(room: RoomModel, scenario: str, args: argparse.Namespace) -> str:
    if scenario == "crac-failure":
        units = [u.strip() for u in args.unit.split(",") if u.strip()]
        for unit in units:
            room.fail_crac(unit)
        return f"{', '.join(units)} failed"
    if scenario == "load-spike":
        room.load_multiplier = args.multiplier
        return f"load multiplier -> {args.multiplier}"
    if scenario == "utility-outage":
        room.cut_utility()
        return "utility lost, UPS on battery"
    if scenario == "setpoint-raise":
        room.set_setpoint(args.setpoint)
        return f"CRAC setpoint -> {args.setpoint}C"
    return "no change"


class StdoutSink:
    def __init__(self, pretty: bool) -> None:
        self.pretty = pretty

    def send(self, snap: dict) -> None:
        if self.pretty:
            print(summarise(snap), flush=True)
        else:
            print(json.dumps(snap), flush=True)

    def close(self) -> None:
        pass


class IotHubSink:
    """One device identity per room; the payload carries the full graph slice."""

    def __init__(self, connection_string: str) -> None:
        from azure.iot.device import IoTHubDeviceClient

        self.client = IoTHubDeviceClient.create_from_connection_string(connection_string)
        self.client.connect()

    def send(self, snap: dict) -> None:
        from azure.iot.device import Message

        msg = Message(json.dumps(snap))
        msg.content_encoding = "utf-8"
        msg.content_type = "application/json"
        msg.custom_properties["roomId"] = snap["room"]["id"]
        self.client.send_message(msg)

    def close(self) -> None:
        self.client.shutdown()


class AdtSink:
    """Skips IoT Hub entirely and patches twin properties in place."""

    def __init__(self) -> None:
        from push import TwinPusher

        self.pusher = TwinPusher()

    def send(self, snap: dict) -> None:
        self.pusher.push(snap)

    def close(self) -> None:
        pass


def build_sink(args: argparse.Namespace):
    if args.sink == "stdout":
        return StdoutSink(pretty=not args.json)
    if args.sink == "iothub":
        cs = os.environ.get("IOTHUB_DEVICE_CONNECTION_STRING")
        if not cs:
            sys.exit("IOTHUB_DEVICE_CONNECTION_STRING is not set. See .env.example.")
        return IotHubSink(cs)
    return AdtSink()


def main() -> None:
    p = argparse.ArgumentParser(description="Server room digital twin simulator")
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p.add_argument("--sink", choices=("stdout", "iothub", "adt"), default="stdout")
    p.add_argument("--interval", type=float, default=5.0, help="model timestep, seconds")
    p.add_argument("--duration", type=float, default=0.0, help="0 runs until Ctrl-C")
    p.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="simulated seconds per real second, e.g. 60 for a fast run",
    )
    p.add_argument("--scenario", choices=SCENARIOS, default="normal")
    p.add_argument("--at", type=float, default=0.0, help="inject the scenario at t=N seconds")
    p.add_argument("--unit", default="crac-01", help="unit id(s) for crac-failure, comma separated")
    p.add_argument("--multiplier", type=float, default=1.6, help="for load-spike")
    p.add_argument("--setpoint", type=float, default=20.0, help="for setpoint-raise")
    p.add_argument("--json", action="store_true", help="stdout sink: emit raw JSON lines")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    room = RoomModel(load_config(args.config), seed=args.seed)
    sink = build_sink(args)
    injected = args.scenario == "normal"

    print(
        f"# {room.display_name}: {len(room.racks)} racks, "
        f"{len(room.crac)} CRAC units, sink={args.sink}, "
        f"scenario={args.scenario}"
        + (f" at t={args.at:.0f}s" if not injected else ""),
        file=sys.stderr,
    )

    try:
        while True:
            room.step(args.interval)

            if not injected and room.elapsed_s >= args.at:
                note = apply_scenario(room, args.scenario, args)
                injected = True
                print(f"# t={room.elapsed_s:.0f}s  scenario injected: {note}", file=sys.stderr)

            sink.send(snapshot(room))

            if args.duration and room.elapsed_s >= args.duration:
                break
            if args.speed > 0:
                time.sleep(args.interval / args.speed)
    except KeyboardInterrupt:
        print("\n# stopped", file=sys.stderr)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
