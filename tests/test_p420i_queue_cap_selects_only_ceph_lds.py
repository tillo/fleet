"""The P420i queue cap must select every Ceph logical drive and never a node's OS disk.

infra-r9mzd.25 / .9 (2026-11-05): each P420i logical drive ran with nr_requests 1013, the
controller's whole command budget, so one stalled TRIM-less consumer SSD (a Ceph OSD) could
hold every slot. The RAID 5 OS disk sda then starved (write await > 1 s in 18 of 241 30-s
samples, p99 3.4 s); the virt-launcher emptyDir on it blocked QEMU, libvirt dropped its
keepalive, and KubeVirt power-cycled the rancher-mgmt VMs (mgmt-3 12x in 16 h). Capping the
Ceph LDs at nr_requests 32 (hpsa IGNORES device/queue_depth writes; with scheduler none,
nr_requests IS the in-flight cap) took sda to 0 starved samples / p99 450 ms and stopped the
power-cycles (measured 2 h each on qui).

The rule matches a POSITIVE hardware property, not a wwn list, so a rebuilt OSD's new LD is
covered without editing it: hpsa reports `raid_level` "RAID 0" for every Ceph LD and "RAID 5"
for every OS LD (measured on qua, qui and quo). A wrong match on sda would cap the OS disk:
that is the failure this test exists to make impossible.

INVENTORY is measured, not assumed (2026-11-05, `cat /sys/block/sd*/device/{vendor,model,
raid_level,wwid}` on each node). ⛔ When rook-ceph-cluster/values.yml gains a wwn (an OSD
rebuild), record that LD here from the node: the test fails until you do, on purpose.
"""

from __future__ import annotations

import fnmatch
import re
import unittest

import yaml

from tests.fleetlib import repo_root

CR_FILE = "harvester-node-config/p420i-queue-cap.yml"
RULE_PATH = "/etc/udev/rules.d/60-mdapi-p420i-queue-cap.rules"

HP, LV = "HP      ", "LOGICAL VOLUME  "
INVENTORY: dict[str, dict[str, dict[str, str]]] = {
    # node -> wwn (without 0x) -> sysfs attributes of the LD's scsi_device (raw, padded)
    "qui": {
        "600508b1001c37b2574376c1adbcd5e1": {"vendor": HP, "model": LV, "raid_level": "RAID 5"},
        "600508b1001cc991a124a501b9d5cf9d": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c2607847a6fd845932500": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c6d83db37249b10c00ae4": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c8ca34f6d3c380d351b39": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001cfc16df93311e0d258612": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
    },
    "quo": {
        "600508b1001c937fd2822b0862f2566c": {"vendor": HP, "model": LV, "raid_level": "RAID 5"},
        "600508b1001cd465f58928a9f278a3a0": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c6fd46d8ed96335934351": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c5052ceee1feb8869f663": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c557ca8f9276a418c3b5f": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c2442949fc101a9ad2bd0": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
    },
    "qua": {
        "600508b1001c314d1208418c509c431a": {"vendor": HP, "model": LV, "raid_level": "RAID 5"},
        "600508b1001c8cc1a03af0d9199d7067": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c6f122eccaa0ee20628b8": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001ccbc0029f54a5bd653ce6": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c2bac499b979768746436": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
        "600508b1001c5ab0133ebea425c6f55e": {"vendor": HP, "model": LV, "raid_level": "RAID 0"},
    },
}
OS_LD = {"qui": "600508b1001c37b2574376c1adbcd5e1", "quo": "600508b1001c937fd2822b0862f2566c",
         "qua": "600508b1001c314d1208418c509c431a"}
# quarto's P408i-a (smartpqi): a different vendor string and raid_level format.
QUARTO_OS = {"vendor": "HPE     ", "model": LV, "raid_level": "RAID-5"}

KEY = re.compile(r'\s*([A-Z]+)(?:\{([^}]*)\})?\s*(==|!=|\+=|:=|=)\s*"([^"]*)"\s*')


def parse_rule(line: str) -> list[tuple[str, str, str, str]]:
    """udev rule line -> [(key, attr, op, value)]; raises on anything unparsable."""
    out = []
    for part in line.split(","):
        m = KEY.fullmatch(part)
        if not m:
            raise ValueError(f"unparsable udev key: {part!r}")
        out.append(m.groups(default=""))
    return out


def rule_lines() -> list[str]:
    cr = yaml.safe_load((repo_root() / CR_FILE).read_text())
    doc = yaml.safe_load(cr["spec"]["contents"])
    files = [f for items in doc["stages"].values() for item in items for f in item.get("files", [])]
    content = next(f["content"] for f in files if f["path"] == RULE_PATH)
    return [ln for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def selects(keys: list[tuple[str, str, str, str]], attrs: dict[str, str]) -> bool:
    """Do the rule's ATTRS== keys select an LD with these scsi_device attributes?

    udev semantics: glob match, and trailing whitespace of the sysfs value is ignored unless
    the pattern itself ends in whitespace. Every ATTRS== key must match and every ATTRS!= key
    must not (one parent device).
    """
    keep = [(attr, op == "==", val) for key, attr, op, val in keys if key == "ATTRS" and op in ("==", "!=")]
    if not any(positive for _, positive, _ in keep):
        raise ValueError("rule has no ATTRS== match keys: it would select every block device")
    return all(fnmatch.fnmatchcase(attrs.get(a, "").rstrip(), v) == positive for a, positive, v in keep)


def assignments(keys) -> list[tuple[str, str]]:
    return [(attr, val) for key, attr, op, val in keys if key == "ATTR" and op == "="]


def ceph_wwns() -> dict[str, list[str]]:
    v = yaml.safe_load((repo_root() / "rook-ceph-cluster" / "values.yml").read_text())
    return {n["name"]: [d["fullpath"].split("wwn-0x")[1] for d in n["devices"] if "wwn-0x" in d["fullpath"]]
            for n in v["cephClusterSpec"]["storage"]["nodes"]}


class P420iQueueCap(unittest.TestCase):
    def setUp(self):
        lines = rule_lines()
        self.assertEqual(len(lines), 1, "one rule line, so its match and its effect stay together")
        self.keys = parse_rule(lines[0])

    def test_every_ceph_ld_in_values_is_inventoried_and_selected(self):
        for node, wwns in ceph_wwns().items():
            for w in wwns:
                with self.subTest(node=node, wwn=w):
                    self.assertIn(w, INVENTORY[node], "record the rebuilt LD's attributes here")
                    self.assertTrue(selects(self.keys, INVENTORY[node][w]))

    def test_no_os_ld_can_be_selected(self):
        for node, w in OS_LD.items():
            with self.subTest(node=node):
                self.assertFalse(selects(self.keys, INVENTORY[node][w]))
        self.assertFalse(selects(self.keys, QUARTO_OS))

    def test_it_sets_none_then_nr_requests_32(self):
        self.assertEqual(assignments(self.keys),
                         [("queue/scheduler", "none"), ("queue/nr_requests", "32")])

    def test_it_acts_on_whole_disks_on_add_and_change(self):
        k = {(key, attr): val for key, attr, op, val in self.keys if op == "=="}
        self.assertEqual(k[("SUBSYSTEM", "")], "block")
        self.assertEqual(k[("ENV", "DEVTYPE")], "disk")
        self.assertEqual(k[("ACTION", "")], "add|change")

    def test_matcher_semantics(self):
        keys = parse_rule('ATTRS{vendor}=="HP", ATTRS{raid_level}=="RAID 0"')
        self.assertTrue(selects(keys, {"vendor": HP, "raid_level": "RAID 0"}))
        self.assertFalse(selects(keys, {"vendor": "HPE     ", "raid_level": "RAID 0"}))
        self.assertFalse(selects(keys, {"vendor": HP, "raid_level": "RAID 5"}))
        negated = parse_rule('ATTRS{vendor}=="HP", ATTRS{raid_level}!="RAID 5"')
        self.assertTrue(selects(negated, {"vendor": HP, "raid_level": "RAID 0"}))
        self.assertFalse(selects(negated, {"vendor": HP, "raid_level": "RAID 5"}))
        with self.assertRaises(ValueError):
            selects(parse_rule('ATTRS{raid_level}!="RAID 5"'), {})
        with self.assertRaises(ValueError):
            selects(parse_rule('SUBSYSTEM=="block"'), {})
        with self.assertRaises(ValueError):
            parse_rule('ATTRS{vendor}=HP')


if __name__ == "__main__":
    unittest.main()
