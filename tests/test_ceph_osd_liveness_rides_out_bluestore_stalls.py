"""The OSD liveness probe must ride out a BlueStore write stall.

Incident (2026-11-04 22:43Z, infra-r9mzd.20): during the osd.4 drain, osd.3 (quo, a
TRIM-less consumer SSD behind the P420i) logged `submit_transact` / `_txc_committed_kv`
latencies of 21-37 s. Its admin socket stopped answering, and Rook's default liveness
probe (`ceph --admin-daemon ... status`, timeout 5 s, period 10 s, failureThreshold 3,
~25-35 s tolerance) SIGKILLed a process that was stalled, not dead (exit 137). The restart
replayed 55 RocksDB WAL files over ~11 min, and 6.7% of objects ran degraded the whole
time. The kill did the damage, not the stall.

Ceph protects the data path on its own: peers mark a silent OSD down after
osd_heartbeat_grace (20 s). The liveness probe exists only to restart a WEDGED
process, so a slow verdict costs nothing. The tolerance must cover the longest stall
measured on these drives with margin, and keep Rook's exec handler (a probe override
without a handler is merged onto Rook's default by the operator).
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

LONGEST_MEASURED_STALL_S = 37   # osd.3 _txc_committed_kv, 2026-11-04 22:43:13Z
MARGIN = 3


def osd_liveness_probe() -> dict:
    values = yaml.safe_load((repo_root() / "rook-ceph-cluster" / "values.yml").read_text())
    return (values["cephClusterSpec"].get("healthCheck", {})
            .get("livenessProbe", {}).get("osd", {}).get("probe", {}))


def tolerance_s(probe: dict) -> int:
    """Seconds of unanswered admin socket before the kubelet kills the OSD."""
    period = probe.get("periodSeconds", 10)
    timeout = probe.get("timeoutSeconds", 5)
    failures = probe.get("failureThreshold", 3)
    return (failures - 1) * period + timeout


class OsdLivenessRidesOutStalls(unittest.TestCase):
    def test_tolerance_covers_the_measured_stall_with_margin(self):
        self.assertGreaterEqual(tolerance_s(osd_liveness_probe()),
                                MARGIN * LONGEST_MEASURED_STALL_S)

    def test_single_probe_timeout_outlasts_a_typical_stall(self):
        # One stalled `status` call must not count as a failure for a ~20 s stall.
        self.assertGreaterEqual(osd_liveness_probe().get("timeoutSeconds", 5), 20)

    def test_rook_keeps_its_own_exec_handler(self):
        probe = osd_liveness_probe()
        for handler in ("exec", "httpGet", "tcpSocket", "grpc"):
            self.assertNotIn(handler, probe)

    def test_a_probe_change_rolls_osds_only_onto_clean_pgs(self):
        # Changing the probe rewrites every OSD Deployment; on these drives a restart
        # can stall, so Rook must wait for clean PGs before the next batch.
        values = yaml.safe_load((repo_root() / "rook-ceph-cluster" / "values.yml").read_text())
        self.assertIs(values["cephClusterSpec"].get("upgradeOSDRequiresHealthyPGs"), True)

    def test_tolerance_counts_the_last_failure_by_its_timeout(self):
        self.assertEqual(tolerance_s({}), 25)
        self.assertEqual(tolerance_s({"periodSeconds": 10, "timeoutSeconds": 20,
                                      "failureThreshold": 10}), 110)


if __name__ == "__main__":
    unittest.main()
