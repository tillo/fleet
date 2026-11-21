"""horizon-0's liveness probe must not kill a stalled-but-alive JVM.

Incident (infra-r9mzd HorizonLic001Wedged, 6 episodes 11-01..11-04; 5 on the Ceph
stall days 11-03/04): the chart default liveness probe (GET /alive, timeout 5 s,
period 10 s, failureThreshold 3, ~25 s tolerance) killed horizon-0's container in
place (last terminated reason Error) at 11-03 20:20Z and 11-04 17:30/20:40/21:40Z,
while the node/storage stalled. Every in-place restart can bring the Pekko DCV
singleton back wedged (LIC-001, the known upstream bug that f/heal/horizon_lic001_heal
cures by deleting the pod). The kill caused the wedge, not the stall.

Readiness (unchanged) already takes a slow horizon-0 out of the Service at once; liveness
only has to catch a JVM that is really hung, so ~2 min costs nothing.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

CHART_DEFAULT = {"periodSeconds": 10, "timeoutSeconds": 5, "failureThreshold": 3}
MIN_TOLERANCE_S = 90


def tolerance_s(probe: dict) -> int:
    p = {**CHART_DEFAULT, **probe}
    return (p["failureThreshold"] - 1) * p["periodSeconds"] + p["timeoutSeconds"]


class HorizonLivenessRidesOutStalls(unittest.TestCase):
    def test_liveness_tolerates_a_minute_and_a_half_of_silence(self):
        values = yaml.safe_load((repo_root() / "horizon" / "values.yml").read_text())
        probe = values.get("livenessProbe") or {}
        self.assertTrue(probe.get("enabled", True))
        self.assertGreaterEqual(tolerance_s(probe), MIN_TOLERANCE_S)

    def test_chart_default_is_the_25_s_that_killed_it(self):
        self.assertEqual(tolerance_s({}), 25)


if __name__ == "__main__":
    unittest.main()
