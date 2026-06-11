#!/usr/bin/env python3
"""Uploads the DTDL models and builds the twin graph described by config/room.yaml.

Idempotent: re-running replaces twins in place rather than failing.

  python seed.py                 upload models + create twins and relationships
  python seed.py --models-only   upload models only
  python seed.py --delete        tear the graph down (twins, then models)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.digitaltwins.core import DigitalTwinsClient
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
CONFIG_PATH = ROOT / "config" / "room.yaml"

# Upload order matters: a model cannot be uploaded before the one it extends.
MODEL_ORDER = [
    "Asset.json",
    "TemperatureSensor.json",
    "Server.json",
    "PDU.json",
    "Rack.json",
    "CRACUnit.json",
    "UPS.json",
    "ServerRoom.json",
]

DTMI = {
    "room": "dtmi:roomtwin:ServerRoom;1",
    "rack": "dtmi:roomtwin:Rack;1",
    "server": "dtmi:roomtwin:Server;1",
    "pdu": "dtmi:roomtwin:PDU;1",
    "sensor": "dtmi:roomtwin:TemperatureSensor;1",
    "crac": "dtmi:roomtwin:CRACUnit;1",
    "ups": "dtmi:roomtwin:UPS;1",
}


def client() -> DigitalTwinsClient:
    url = os.environ.get("AZURE_DIGITALTWINS_URL")
    if not url:
        sys.exit("AZURE_DIGITALTWINS_URL is not set. See .env.example.")
    return DigitalTwinsClient(url, DefaultAzureCredential())


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def upload_models(dt: DigitalTwinsClient) -> None:
    payloads = []
    for name in MODEL_ORDER:
        with open(MODELS_DIR / name, "r", encoding="utf-8") as fh:
            payloads.append(json.load(fh))
    try:
        dt.create_models(payloads)
        print(f"uploaded {len(payloads)} models")
    except HttpResponseError as exc:
        if "ModelIdAlreadyExists" in str(exc):
            print("models already present, skipping upload")
        else:
            raise


def upsert(dt: DigitalTwinsClient, twin_id: str, model_id: str, props: dict) -> None:
    twin = {"$metadata": {"$model": model_id}, **props}
    dt.upsert_digital_twin(twin_id, twin)
    print(f"  twin  {twin_id:<16} {model_id}")


def relate(dt: DigitalTwinsClient, source: str, name: str, target: str) -> None:
    rel_id = f"{source}-{name}-{target}"
    dt.upsert_relationship(
        source,
        rel_id,
        {"$relationshipId": rel_id, "$sourceId": source, "$relationshipName": name,
         "$targetId": target},
    )
    print(f"  rel   {source} --{name}--> {target}")


def build_graph(dt: DigitalTwinsClient, cfg: dict) -> None:
    rc = cfg["room"]
    room_id = rc["id"]

    upsert(dt, room_id, DTMI["room"], {
        "assetId": room_id,
        "floorAreaM2": rc["floorAreaM2"],
        "designCapacityKw": rc["designCapacityKw"],
        "setpointC": rc["setpointC"],
        "ashraeClass": rc["ashraeClass"],
        "status": "nominal",
    })

    for r in cfg["racks"]:
        upsert(dt, r["id"], DTMI["rack"], {
            "assetId": r["id"],
            "uHeight": r["uHeight"],
            "positionX": r["positionX"],
            "positionY": r["positionY"],
            "aisle": r["aisle"],
            "maxInletTempC": r["maxInletTempC"],
            "shutdownTempC": r["shutdownTempC"],
            "airflowCfm": r["airflowCfm"],
        })
        relate(dt, room_id, "contains", r["id"])

        pdu = r["pdu"]
        upsert(dt, pdu["id"], DTMI["pdu"], {
            "assetId": pdu["id"],
            "capacityKw": pdu["capacityKw"],
        })
        relate(dt, r["id"], "poweredBy", pdu["id"])

        for s in r["sensors"]:
            upsert(dt, s["id"], DTMI["sensor"], {
                "assetId": s["id"],
                "placement": s["placement"],
            })
            relate(dt, r["id"], "contains", s["id"])

        for s in r["servers"]:
            upsert(dt, s["id"], DTMI["server"], {
                "assetId": s["id"],
                "hostname": s["hostname"],
                "ratedPowerKw": s["ratedPowerKw"],
                "uSize": s["uSize"],
                "throttling": False,
            })
            relate(dt, r["id"], "contains", s["id"])

    for c in cfg["crac"]:
        upsert(dt, c["id"], DTMI["crac"], {
            "assetId": c["id"],
            "ratedCoolingKw": c["ratedCoolingKw"],
            "setpointC": c["setpointC"],
            "airflowCfm": c["airflowCfm"],
            "operatingState": "running",
        })
        relate(dt, c["id"], "cools", room_id)
        relate(dt, room_id, "cooledBy", c["id"])

    u = cfg["ups"]
    upsert(dt, u["id"], DTMI["ups"], {
        "assetId": u["id"],
        "capacityKwh": u["capacityKwh"],
        "ratedPowerKw": u["ratedPowerKw"],
    })
    relate(dt, room_id, "backedBy", u["id"])


def delete_graph(dt: DigitalTwinsClient, cfg: dict) -> None:
    ids = [cfg["room"]["id"], cfg["ups"]["id"]]
    ids += [c["id"] for c in cfg["crac"]]
    for r in cfg["racks"]:
        ids += [r["id"], r["pdu"]["id"]]
        ids += [s["id"] for s in r["sensors"]]
        ids += [s["id"] for s in r["servers"]]

    for twin_id in ids:
        try:
            for rel in dt.list_relationships(twin_id):
                dt.delete_relationship(twin_id, rel["$relationshipId"])
        except ResourceNotFoundError:
            continue
    for twin_id in ids:
        try:
            dt.delete_digital_twin(twin_id)
            print(f"  deleted {twin_id}")
        except ResourceNotFoundError:
            pass
    for model_id in reversed([m for m in DTMI.values()] + ["dtmi:roomtwin:Asset;1"]):
        try:
            dt.delete_model(model_id)
        except HttpResponseError:
            pass


def main() -> None:
    p = argparse.ArgumentParser(description="Seed the Azure Digital Twins graph")
    p.add_argument("--models-only", action="store_true")
    p.add_argument("--delete", action="store_true")
    args = p.parse_args()

    dt = client()
    cfg = load_config()

    if args.delete:
        delete_graph(dt, cfg)
        print("graph removed")
        return

    upload_models(dt)
    if args.models_only:
        return
    build_graph(dt, cfg)
    print("graph ready")


if __name__ == "__main__":
    main()
