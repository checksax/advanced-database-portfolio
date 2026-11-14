"""
Portfolio 5: start, stop and inspect the local MongoDB clusters.

Two independent clusters (run one at a time on a small laptop):

  rs     3-member replica set "rs0"               ports 27018, 27019, 27020
  shard  sharded cluster: config server "cfg"     port  27030
                          shard "shardA"          port  27031
                          shard "shardB"          port  27032
                          mongos router           port  27040

Every process is a separate mongod/mongos with its own data directory under
cluster/data, bound to 127.0.0.1 only, with a 256 MB WiredTiger cache.
enableTestCommands=1 is set ONLY so the demos can pause replication on a
secondary with a fail point (to make replication lag visible). Never use it
in production.

Usage:
  python cluster.py start rs | stop rs | status rs | clean rs
  python cluster.py start shard | stop shard | status shard | clean shard
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

BIN = Path(r"C:\Program Files\MongoDB\Server\9.0\bin")
HERE = Path(__file__).resolve().parent
ROOT = HERE / "cluster"
HOST = "127.0.0.1"
CACHE_GB = "0.25"

NODES = {
    "rs": [
        {"name": "rs0-1", "port": 27018, "replset": "rs0", "priority": 2},
        {"name": "rs0-2", "port": 27019, "replset": "rs0", "priority": 1},
        {"name": "rs0-3", "port": 27020, "replset": "rs0", "priority": 1},
    ],
    "shard": [
        {"name": "cfg", "port": 27030, "replset": "cfg", "role": "--configsvr"},
        {"name": "shardA", "port": 27031, "replset": "shardA", "role": "--shardsvr"},
        {"name": "shardB", "port": 27032, "replset": "shardB", "role": "--shardsvr"},
    ],
}
MONGOS = {"name": "mongos", "port": 27040}
DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (Windows)


def node(port: int) -> MongoClient:
    return MongoClient(HOST, port, directConnection=True, serverSelectionTimeoutMS=3000)


def alive(port: int) -> bool:
    try:
        node(port).admin.command("ping")
        return True
    except PyMongoError:
        return False


def start_mongod(n: dict) -> None:
    if alive(n["port"]):
        return
    data = ROOT / "data" / n["name"]
    data.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    cmd = [str(BIN / "mongod.exe"), "--port", str(n["port"]), "--bind_ip", HOST,
           "--dbpath", str(data), "--logpath", str(ROOT / "logs" / f"{n['name']}.log"), "--logappend",
           "--replSet", n["replset"], "--wiredTigerCacheSizeGB", CACHE_GB, "--oplogSize", "256",
           "--setParameter", "enableTestCommands=1"]
    if n.get("role"):
        cmd.append(n["role"])
    subprocess.Popen(cmd, creationflags=DETACHED, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_mongos() -> None:
    if alive(MONGOS["port"]):
        return
    cmd = [str(BIN / "mongos.exe"), "--port", str(MONGOS["port"]), "--bind_ip", HOST,
           "--configdb", f"cfg/{HOST}:27030", "--logpath", str(ROOT / "logs" / "mongos.log"), "--logappend"]
    subprocess.Popen(cmd, creationflags=DETACHED, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(port: int, timeout: float = 60) -> None:
    start = time.time()
    while not alive(port):
        if time.time() - start > timeout:
            raise TimeoutError(f"nothing answering on port {port} - see cluster/logs")
        time.sleep(0.5)


def wait_primary(port: int, timeout: float = 60) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            if node(port).admin.command("hello").get("isWritablePrimary"):
                return
        except PyMongoError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"no primary on {port}")


def initiate(replset: str, members: list[dict], configsvr: bool = False) -> None:
    first = node(members[0]["port"])
    try:
        first.admin.command("replSetGetStatus")
        return                                                   # already initiated
    except OperationFailure:
        pass
    config = {"_id": replset,
              "members": [{"_id": i, "host": f"{HOST}:{m['port']}", "priority": m.get("priority", 1)}
                          for i, m in enumerate(members)]}
    if configsvr:
        config["configsvr"] = True
    first.admin.command("replSetInitiate", config)
    wait_primary(members[0]["port"])


def start(group: str) -> None:
    for n in NODES[group]:
        start_mongod(n)
    for n in NODES[group]:
        wait_for(n["port"])
    if group == "rs":
        initiate("rs0", NODES["rs"])
    else:
        for n in NODES["shard"]:
            initiate(n["replset"], [n], configsvr=n["name"] == "cfg")
        start_mongos()
        wait_for(MONGOS["port"])
        router = MongoClient(HOST, MONGOS["port"])
        have = {s["_id"] for s in router.admin.command("listShards")["shards"]}
        for n in NODES["shard"][1:]:
            if n["replset"] not in have:
                router.admin.command("addShard", f"{n['replset']}/{HOST}:{n['port']}", name=n["replset"])
    status(group)


def stop(group: str) -> None:
    ports = ([MONGOS["port"]] if group == "shard" else []) + [n["port"] for n in NODES[group]]
    for port in ports:
        if alive(port):
            try:
                node(port).admin.command("shutdown", force=True)
            except PyMongoError:
                pass                                             # connection closes as the server exits
    time.sleep(2)
    status(group)


def clean(group: str) -> None:
    stop(group)
    for n in NODES[group]:
        shutil.rmtree(ROOT / "data" / n["name"], ignore_errors=True)
    print(f"data directories for '{group}' removed")


def status(group: str) -> None:
    for n in NODES[group] + ([MONGOS] if group == "shard" else []):
        if not alive(n["port"]):
            print(f"  {n['name']:<8} :{n['port']}  DOWN")
            continue
        hello = node(n["port"]).admin.command("hello")
        role = "mongos" if hello.get("msg") == "isdbgrid" else \
            "PRIMARY" if hello.get("isWritablePrimary") else "SECONDARY" if hello.get("secondary") else "OTHER"
        pid = node(n["port"]).admin.command("serverStatus")["pid"]
        print(f"  {n['name']:<8} :{n['port']}  {role:<9} pid {pid}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("start", "stop", "status", "clean") or sys.argv[2] not in NODES:
        print(__doc__)
        sys.exit(1)
    {"start": start, "stop": stop, "status": status, "clean": clean}[sys.argv[1]](sys.argv[2])
