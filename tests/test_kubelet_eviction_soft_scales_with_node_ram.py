"""The kubelet's soft memory eviction line must scale with the node's RAM (infra-y6ac, infra-7ku6).

kubelet-memory-reserve.yml set eviction-soft memory.available<14Gi on every node. That was
calibrated on the 94 GiB Gen8s (qua's 2026-10-26 stall sat at ~12.8 GiB). quarto has 47 GiB, so
14Gi is 30 % of it: on 2026-11-21 afternoon quarto sat in MemoryPressure (NoSchedule taint) with
19 GiB available, refusing pods on a node that was nowhere near trouble. quarto gets the same line
as a share of its RAM (7Gi). Everything else in the drop-in stays identical, so the two profiles
cannot drift apart except in the one value that is meant to differ.

Both CRs select on the SAME label key with different values, so a node can match only one of them,
and each has its own /oem filename (node-manager deletes a CR's file from every node its selector
stops matching; two CRs sharing a filename would fight over it).
"""

from __future__ import annotations

import re
import unittest

import yaml

import fleetlib

DEFAULT = "harvester-node-config/kubelet-memory-reserve.yml"
SMALL = "harvester-node-config/kubelet-memory-reserve-48gib.yml"
LABEL = "mdapi.ch/kubelet-memory-reserve"
DROPIN = "/etc/rancher/rke2/config.yaml.d/99-zz-mdapi-kubelet-reserve.yaml"

# MemTotal measured 2026-11-21 (GiB). The label value each node carries picks its profile.
NODE_RAM_GIB = {"qua": 94.3, "qui": 94.3, "quo": 94.3, "quarto": 46.7}
NODE_PROFILE = {"qua": DEFAULT, "qui": DEFAULT, "quo": DEFAULT, "quarto": SMALL}


def _cr(rel: str) -> dict:
    (cloudinit,) = fleetlib.load_docs(rel)
    return cloudinit


def _kubelet_args(rel: str) -> list[str]:
    stages = yaml.safe_load(_cr(rel)["spec"]["contents"])["stages"]
    (step,) = stages["initramfs"]
    (f,) = [f for f in step["files"] if f["path"] == DROPIN]
    return yaml.safe_load(f["content"])["kubelet-arg+"]


def _gib(quantity: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)Gi", quantity)
    assert m, quantity
    return float(m.group(1))


def _soft_memory_gib(rel: str) -> float:
    (soft,) = [a for a in _kubelet_args(rel) if a.startswith("eviction-soft=")]
    return _gib(re.search(r"memory\.available<([^,]+)", soft).group(1))


class EvictionSoftScalesWithRam(unittest.TestCase):
    def test_every_node_soft_line_is_the_same_share_of_its_ram(self):
        reference = _soft_memory_gib(DEFAULT) / NODE_RAM_GIB["qua"]
        for node, ram in NODE_RAM_GIB.items():
            with self.subTest(node=node):
                share = _soft_memory_gib(NODE_PROFILE[node]) / ram
                self.assertAlmostEqual(share, reference, delta=0.02)

    def test_profiles_differ_only_in_the_soft_memory_line(self):
        def strip(args):
            return [re.sub(r"memory\.available<[^,]+", "memory.available<X", a)
                    if a.startswith("eviction-soft=") else a for a in args]
        self.assertEqual(strip(_kubelet_args(SMALL)), strip(_kubelet_args(DEFAULT)))

    def test_a_node_matches_exactly_one_profile(self):
        default, small = _cr(DEFAULT)["spec"], _cr(SMALL)["spec"]
        self.assertEqual(set(default["matchSelector"]), {LABEL})
        self.assertEqual(set(small["matchSelector"]), {LABEL})
        self.assertNotEqual(default["matchSelector"][LABEL], small["matchSelector"][LABEL])
        self.assertNotEqual(default["filename"], small["filename"])


if __name__ == "__main__":
    unittest.main()
