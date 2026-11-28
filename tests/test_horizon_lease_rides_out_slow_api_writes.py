"""horizon-0's Pekko singleton leases must survive a few seconds of slow API writes.

Incident (infra-x9ghc HorizonLic001Wedged, 8 episodes 11-05..11-19): every wedge
starts with one lease heartbeat timing out — 2026-11-19 17:10:50Z "Timed out updating
lease [...dcv-policy-manager] ... Is the API server up?" — after which every singleton
drops Oldest -> AcquiringLease and the DCV singleton proxy never re-identifies
(LIC-001, cured only by f/heal/horizon_lic001_heal deleting the pod). The image's
pekko.conf keeps the library defaults: lease-operation-timeout 5 s, and an empty
api-server-request-timeout resolves to 2/5 of that = 2 s. A single 2 s lease PUT
(an etcd failed-proposal blip: 128 at 11-14 03:30Z, 189 at 11-18 16:20Z, 7 at
11-19 17:05Z) is enough to drop every singleton.

Limits (pekko-lease-kubernetes 1.2.1 / pekko-coordination 1.6.0 reference.conf):
api-server-request-timeout must be < lease-operation-timeout (KubernetesSettings
require), and an operation should finish inside the 12 s heartbeat-interval
(heartbeat-timeout 120 s / 10), so the next heartbeat does not queue behind it.
heartbeat-timeout stays 120 s: it is how long a NEW pod (new IP, new owner) waits for
the old lease after a pod replacement.
"""

from __future__ import annotations

import re
import unittest

import yaml

from tests.fleetlib import repo_root

PREFIX = "pekko.coordination.lease.kubernetes."
DEFAULT_OPERATION_S = 5
HEARTBEAT_INTERVAL_S = 12  # heartbeat-timeout 120 s / 10, left at the default
MIN_REQUEST_S = 8


def seconds(value: str) -> int:
    m = re.fullmatch(r"(\d+)s", value)
    if not m:
        raise ValueError(f"expected '<n>s', got {value!r}")
    return int(m.group(1))


def lease_settings(values: dict) -> dict[str, int]:
    """The lease -D overrides Horizon's entrypoint passes via `exec java $JAVA_OPTS`."""
    env = {e["name"]: e.get("value", "") for e in values.get("environment") or []}
    found = {}
    for flag in env.get("JAVA_OPTS", "").split():
        if flag.startswith("-D" + PREFIX):
            key, _, value = flag[2 + len(PREFIX):].partition("=")
            found[key] = seconds(value)
    return found


def effective(settings: dict[str, int]) -> tuple[int, float]:
    operation = settings.get("lease-operation-timeout", DEFAULT_OPERATION_S)
    request = settings.get("api-server-request-timeout", operation * 2 / 5)
    return operation, request


class HorizonLeaseRidesOutSlowApiWrites(unittest.TestCase):
    def setUp(self):
        values = yaml.safe_load((repo_root() / "horizon" / "values.yml").read_text())
        self.operation, self.request = effective(lease_settings(values))

    def test_a_lease_api_request_may_take_eight_seconds(self):
        self.assertGreaterEqual(self.request, MIN_REQUEST_S)

    def test_request_timeout_is_below_operation_timeout(self):
        self.assertLess(self.request, self.operation)

    def test_operation_finishes_inside_one_heartbeat_interval(self):
        self.assertLess(self.operation, HEARTBEAT_INTERVAL_S)

    def test_library_default_is_the_2_s_that_wedged_it(self):
        self.assertEqual(effective({}), (5, 2.0))

    def test_malformed_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            seconds("10 seconds")


if __name__ == "__main__":
    unittest.main()
