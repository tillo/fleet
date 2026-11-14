"""The Rook operator's memory limit must cover its full-reconcile peak with margin.

2026-06-21: 512Mi OOMKilled during CRUSH/topology recompute (18 OSDs) -> 1Gi.
2026-11-04/05 (infra-g45ql, infra-r9mzd.20): with 21 OSDs and an OSD rebuild in flight, the
operator was OOMKilled repeatedly at 1Gi (one container peaked at 979 MiB before it). Every
OSD purge/prepare triggers a full reconcile, and an OOM mid-prepare leaves a rebuild
half-done. User decision 2026-11-05: 1.5 GiB. The REQUEST stays 256Mi (node memory may not
be reserved), so the extra is used only during the spikes.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

MEASURED_PEAK_MIB = 979   # rook-ceph-operator-…-zbtlg, max_over_time 24h, 2026-11-05
UNITS = {"Mi": 1, "Gi": 1024}


def mib(q: str) -> float:
    for suffix, factor in UNITS.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * factor
    raise ValueError(f"unsupported quantity {q!r}")


def resources() -> dict:
    return yaml.safe_load((repo_root() / "rook-ceph" / "values.yml").read_text())["resources"]


class RookOperatorMemory(unittest.TestCase):
    def test_limit_is_the_user_decision_and_covers_the_peak(self):
        limit = mib(resources()["limits"]["memory"])
        self.assertEqual(limit, 1536)
        self.assertGreaterEqual(limit, 1.5 * MEASURED_PEAK_MIB)

    def test_request_reserves_nothing_extra_on_the_node(self):
        self.assertEqual(resources()["requests"]["memory"], "256Mi")

    def test_quantities(self):
        self.assertEqual(mib("1Gi"), 1024)
        self.assertEqual(mib("1536Mi"), 1536)
        with self.assertRaises(ValueError):
            mib("2G")


if __name__ == "__main__":
    unittest.main()
