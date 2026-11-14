"""kube-state-metrics must not list/watch Secrets.

SA token audit 2026-11-10 (infra-p6q07.4): the chart-default `secrets` collector gave
KSM's ServiceAccount list/watch on every Secret in the cluster, including the two
long-lived cluster-admin token Secrets, so a compromised KSM pod was cluster-admin.
Nothing consumed the output: 0 references to `kube_secret_*` in fleet, windmill, the
live VMRules/PrometheusRules or the dashboard ConfigMaps (measured 2026-11-11).

The chart derives both `--resources` and its ClusterRole from `collectors`, so dropping
the entry removes the RBAC rule too. helm-controller REPLACES list values from a
HelmChartConfig, so the list must restate every collector we still want.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import load_docs

CONFIG = "rancher-monitoring-config/rancher-monitoring-helmchartconfig.yml"

# Collectors that alerts and dashboards depend on: dropping one must be deliberate.
MUST_KEEP = {"pods", "deployments", "statefulsets", "daemonsets", "jobs", "cronjobs",
             "nodes", "persistentvolumeclaims", "persistentvolumes", "namespaces",
             "replicasets", "configmaps", "services", "ingresses"}


def collectors() -> list[str]:
    (doc,) = [d for d in load_docs(CONFIG) if d.get("kind") == "HelmChartConfig"]
    values = yaml.safe_load(doc["spec"]["valuesContent"])
    return values["kube-state-metrics"]["collectors"]


class KubeStateMetricsCollectors(unittest.TestCase):
    def test_secrets_collector_is_off(self):
        self.assertNotIn("secrets", collectors())

    def test_workload_collectors_are_still_on(self):
        self.assertEqual(set(), MUST_KEEP - set(collectors()))


if __name__ == "__main__":
    unittest.main()
