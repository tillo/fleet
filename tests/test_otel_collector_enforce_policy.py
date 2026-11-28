"""The otel-collector admits exactly the known OTLP emitters, and denies the rest.

infra-o4i3m.9 (2026-11-10): the O2 otel-collector (ns openobserve) takes OTLP on 4317/4318
with no auth, because ingress-nginx cannot send an auth header, so any pod could inject
fake spans into O2 traces. learn.otel-collector recorded every caller outside the five
known emitters from 2026-11-16 22:17Z for ~33 h (journald suppression 0 on every node):
two one-off connections from short-lived job pods within a minute of the policy's
creation, and nothing after. The emitters configured in fleet are exactly the ones below (the five learnt, plus iast-sink added 2026-11-23).

Pinned here:
  * Ingress only; exactly the known emitter workloads are allowed, each by namespace AND
    pod label.
  * The end is Log then Deny: a block is answerable from netpol_flows. No unconditional
    Allow anywhere (the learn policy's trailing Allow must not survive).
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

POLICY = repo_root() / "openobserve" / "otel-collector-netpol-enforce.yml"
LEARN = repo_root() / "openobserve" / "otel-collector-netpol-learn.yml"
EMITTERS = {
    ("kube-system", 'app.kubernetes.io/name == "rke2-ingress-nginx"'),
    ("keycloak", 'app == "keycloak"'),
    ("openwebui", 'app == "openwebui"'),
    ("formbricks-kustodyan", 'app == "spicedb"'),
    ("monitoring", 'app == "grafana"'),
    # infra-7rog.41.1: the IAST sink forwards Datadog-protocol spans as OTLP.
    ("iast", 'app == "iast-sink"'),
}


def spec() -> dict:
    return yaml.safe_load(POLICY.read_text())["spec"]


def allowed_sources() -> set[tuple[str, str]]:
    found = set()
    for r in spec()["ingress"]:
        src = r.get("source") or {}
        if r.get("action") == "Allow" and src:
            ns = src["namespaceSelector"].split("==", 1)[1].strip().strip('"')
            found.add((ns, src["selector"]))
    return found


class OtelCollectorEnforcePolicy(unittest.TestCase):
    def test_learn_policy_is_gone(self):
        self.assertFalse(LEARN.exists())

    def test_selects_the_collector_ingress_only(self):
        self.assertEqual(spec()["selector"], 'app == "otel-collector"')
        self.assertEqual(spec()["types"], ["Ingress"])

    def test_exactly_the_known_emitters_are_allowed(self):
        self.assertEqual(allowed_sources(), EMITTERS)

    def test_every_allow_names_both_namespace_and_pod(self):
        for r in spec()["ingress"]:
            if r.get("action") == "Allow":
                with self.subTest(rule=r):
                    self.assertIn("namespaceSelector", r["source"])
                    self.assertIn("selector", r["source"])

    def test_ends_with_log_then_deny_and_never_allows_unconditionally(self):
        rules = spec()["ingress"]
        self.assertEqual(rules[-2:], [{"action": "Log"}, {"action": "Deny"}])
        self.assertNotIn({"action": "Allow"}, rules)


if __name__ == "__main__":
    unittest.main()
