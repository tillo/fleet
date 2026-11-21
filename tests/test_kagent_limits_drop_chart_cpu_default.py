"""kagent's resources must not inherit the chart's CPU limits.

Incident (2026-11-05, infra-r9mzd ContainerCpuThrottling on kagent/postgresql, firing 48 h):
kagent/fleet.yaml writes `limits: {memory: ...}` only, meaning "memory cap, no CPU cap"
(the estate pattern: falco 091a70c1, dolt !1817). Helm MERGES values maps, so the
chart's default `limits.cpu` survived underneath: postgres ran with cpu 500m (controller
2, ui 1). At 17 mCPU average the bundled postgres was CFS-throttled in 26% of periods,
i.e. every checkpoint/autovacuum burst was stalled. Only an explicit `cpu: null` deletes
a chart default key.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root


def limits_maps(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if k == "limits" and isinstance(v, dict):
                yield p, v
            yield from limits_maps(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from limits_maps(v, f"{path}[{i}]")


class KagentLimitsDropChartCpuDefault(unittest.TestCase):
    def test_every_limits_map_states_cpu_or_deletes_it(self):
        values = yaml.safe_load((repo_root() / "kagent" / "fleet.yaml").read_text())["helm"]["values"]
        found = list(limits_maps(values))
        self.assertGreaterEqual(len(found), 3)
        for path, lim in found:
            with self.subTest(path=path):
                self.assertIn("cpu", lim, "omitting cpu keeps the chart's CPU limit; write cpu: null")

    def test_limits_maps_walks_nested_values(self):
        got = dict(limits_maps({"a": {"resources": {"limits": {"memory": "1"}}}, "b": [{"limits": {}}]}))
        self.assertEqual(set(got), {"a.resources.limits", "b[0].limits"})


if __name__ == "__main__":
    unittest.main()
