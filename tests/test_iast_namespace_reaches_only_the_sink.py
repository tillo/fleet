"""Nothing in ns `iast` can reach a SaaS intake; the sink reaches only DNS and the collector.

infra-7rog.41.1 (2026-11-23): IAST uses Datadog's OPEN-SOURCE tracers with no Datadog
backend (user rule: no SaaS, even anonymised). The tracer is pointed at iast-sink by
DD_TRACE_AGENT_URL, but a forgotten variable, a default or a future tracer feature could
send to *.datadoghq.* instead. The network, not the configuration, is the guarantee:
  * every pod in the namespace except the sink may egress to DNS and the sink's :8126 only;
  * the sink may egress to DNS and the otel-collector's OTLP/HTTP :4318 only.
Neither policy may carry an ipBlock egress (that is how "the internet" gets admitted).
"""
from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

POLICY = repo_root() / "iast-sink" / "iast-netpol.yml"
DNS = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
       "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}


def policies() -> dict[str, dict]:
    return {d["metadata"]["name"]: d["spec"] for d in yaml.safe_load_all(POLICY.read_text()) if d}


def egress_peers(spec: dict) -> list[tuple[dict, list]]:
    return [(peer, rule.get("ports", [])) for rule in spec["egress"] for peer in rule["to"]]


class IastNamespaceReachesOnlyTheSink(unittest.TestCase):
    def test_tracers_policy_selects_every_pod_but_the_sink(self):
        sel = policies()["iast-tracers-sink-only"]["podSelector"]
        self.assertEqual(sel, {"matchExpressions": [
            {"key": "app", "operator": "NotIn", "values": ["iast-sink"]}]})
        self.assertIn("Egress", policies()["iast-tracers-sink-only"]["policyTypes"])

    def test_tracers_egress_is_dns_and_the_sink_only(self):
        peers = [p for p, _ in egress_peers(policies()["iast-tracers-sink-only"])]
        self.assertEqual(peers, [DNS, {"podSelector": {"matchLabels": {"app": "iast-sink"}}}])

    def test_sink_egress_is_dns_and_the_collector_only(self):
        spec = policies()["iast-sink"]
        self.assertIn("Egress", spec["policyTypes"])
        peers = egress_peers(spec)
        self.assertEqual([p for p, _ in peers][0], DNS)
        collector, ports = peers[1]
        self.assertEqual(collector["podSelector"], {"matchLabels": {"app": "otel-collector"}})
        self.assertEqual(ports, [{"protocol": "TCP", "port": 4318}])
        self.assertEqual(len(peers), 2)

    def test_no_egress_ip_block_anywhere(self):
        for name, spec in policies().items():
            for peer, _ in egress_peers(spec):
                with self.subTest(policy=name):
                    self.assertNotIn("ipBlock", peer)


if __name__ == "__main__":
    unittest.main()
