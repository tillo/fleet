"""Every OSD's BlueStore DB/WAL device must be a stable, unshared, ceph-volume-safe partition.

infra-r9mzd.20 (2026-11-05) moves each consumer-SSD OSD's RocksDB DB/WAL onto a 12 GiB
FusionIO partition (`config.metadataDevice`), one OSD at a time. Three ways that goes
wrong without erroring at review time:

* `/dev/fioaN` is not a stable name: with two cards the enumeration flips (proven on quo
  2026-08-21), and an OSD would open ANOTHER OSD's partition as its DB. Only
  `/dev/disk/by-partlabel/...` (68-iomemory-partlinks udev rule) is stable.
* ceph-volume rejects any partition whose GPT label CONTAINS "ceph" as a legacy
  ceph-disk member (quo-ceph-fio2 self-sabotaged 2026-08-22), and the OSD never comes up.
* Two OSDs naming the same DB partition: the second mkfs overwrites the first OSD's DB.

The label scheme is `<node>-db-bay<N>`, N = the P420i bay of the data drive.
"""

from __future__ import annotations

import re
import unittest

import yaml

from tests.fleetlib import repo_root

PARTLABEL = re.compile(r"^/dev/disk/by-partlabel/(?P<node>[a-z]+)-db-bay(?P<bay>[1-8])$")


def nodes() -> list[dict]:
    v = yaml.safe_load((repo_root() / "rook-ceph-cluster" / "values.yml").read_text())
    return v["cephClusterSpec"]["storage"]["nodes"]


def db_devices(node: dict) -> list[str]:
    return [d["config"]["metadataDevice"] for d in node.get("devices", [])
            if "metadataDevice" in (d.get("config") or {})]


def problems(node_name: str, dbs: list[str]) -> list[str]:
    out = []
    for path in dbs:
        m = PARTLABEL.match(path)
        if not m:
            out.append(f"{path}: not /dev/disk/by-partlabel/<node>-db-bay<N>")
        elif m["node"] != node_name:
            out.append(f"{path}: label names node {m['node']}, listed under {node_name}")
        if "ceph" in path.rsplit("/", 1)[-1]:
            out.append(f"{path}: label contains 'ceph' (ceph-volume rejects it)")
    out += [f"{p}: shared by {dbs.count(p)} OSDs" for p in sorted(set(dbs)) if dbs.count(p) > 1]
    return out


class OsdDbDevices(unittest.TestCase):
    def test_every_db_device_is_a_stable_unshared_node_partlabel(self):
        for node in nodes():
            with self.subTest(node=node["name"]):
                self.assertEqual(problems(node["name"], db_devices(node)), [])

    def test_the_rebuild_pilot_has_its_db_on_fusionio(self):
        qui = next(n for n in nodes() if n["name"] == "qui")
        self.assertIn("/dev/disk/by-partlabel/qui-db-bay2", db_devices(qui))

    def test_problems_catches_each_failure_mode(self):
        self.assertEqual(problems("qui", ["/dev/disk/by-partlabel/qui-db-bay2"]), [])
        self.assertEqual(len(problems("qui", ["/dev/fioa4"])), 1)
        self.assertEqual(len(problems("qui", ["/dev/disk/by-partlabel/quo-db-bay2"])), 1)
        self.assertEqual(len(problems("qui", ["/dev/disk/by-partlabel/qui-db-bay2"] * 2)), 1)
        self.assertTrue(any("ceph" in p for p in problems("qui", ["/dev/disk/by-partlabel/ceph-db"])))


if __name__ == "__main__":
    unittest.main()
