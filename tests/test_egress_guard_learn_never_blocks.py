"""The egress-guard learn tier must log public egress and block NOTHING.

infra-wk4ux.5 / infra-az8.4 step 1: a Calico tier evaluated before the default tier, holding
one GlobalNetworkPolicy that logs every NEW connection to a public address and then passes
the packet on to the default tier unchanged. Proven on a scratch namespace 2026-11-10: with
the tier's Pass, a pod with no policy still reached 1.1.1.1 and the cluster, and a pod whose
own k8s NetworkPolicy denied public egress stayed denied.

What breaks the cluster if it regresses:
  * a selected endpoint default-DENIES at the end of a tier, so the policy must end in an
    unconditional Pass and the Tier's defaultAction must be Pass as well (belt and braces);
  * any Deny/Allow here would override the namespace's own policies (Allow ends evaluation
    before the default tier), so only Log and Pass are permitted;
  * a private range missing from notNets logs all in-cluster traffic and blows the per-node
    journald budget (~9 lines/s from one namespace in the jump learn window).
"""

from __future__ import annotations

import ipaddress
import unittest

import yaml

from tests.fleetlib import repo_root

POLICY = repo_root() / "egress-guard" / "egress-guard-learn.yml"
NON_PUBLIC = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")]
MUST_EXCLUDE = {"kube-system", "longhorn-system", "harvester-system", "metallb-system"}
MUST_EXCLUDE_PREFIX = {"cattle-", "fleet-", "cluster-fleet-"}


def docs() -> dict[str, dict]:
    return {d["kind"]: d for d in yaml.safe_load_all(POLICY.read_text()) if d}


class EgressGuardLearn(unittest.TestCase):
    def setUp(self):
        self.d = docs()
        self.gnp = self.d["GlobalNetworkPolicy"]
        self.rules = self.gnp["spec"]["egress"]

    def test_tier_passes_by_default_and_runs_before_the_default_tier(self):
        tier = self.d["Tier"]["spec"]
        self.assertEqual(tier["defaultAction"], "Pass")
        self.assertLess(tier["order"], 1_000_000)  # Calico's default tier order

    def test_policy_lives_in_the_tier_and_is_egress_only(self):
        self.assertEqual(self.gnp["spec"]["tier"], self.d["Tier"]["metadata"]["name"])
        self.assertTrue(self.gnp["metadata"]["name"].startswith(self.d["Tier"]["metadata"]["name"] + "."))
        self.assertEqual(self.gnp["spec"]["types"], ["Egress"])
        self.assertNotIn("ingress", self.gnp["spec"])

    def test_only_log_and_pass_and_last_is_unconditional_pass(self):
        self.assertEqual({r["action"] for r in self.rules}, {"Log", "Pass"})
        self.assertEqual(self.rules[-1], {"action": "Pass"})

    def test_exactly_one_log_rule_and_it_matches_public_destinations_only(self):
        logs = [r for r in self.rules if r["action"] == "Log"]
        self.assertEqual(len(logs), 1)
        dest = logs[0]["destination"]
        self.assertNotIn("nets", dest)
        excluded = [ipaddress.ip_network(n) for n in dest["notNets"]]
        for net in NON_PUBLIC:
            self.assertTrue(any(net.subnet_of(e) for e in excluded), f"{net} would be logged")
        for public in ("1.1.1.1", "31.3.128.59", "8.8.8.8"):
            self.assertFalse(any(ipaddress.ip_address(public) in e for e in excluded),
                             f"{public} would NOT be logged")

    def test_log_rule_is_not_narrowed_by_protocol_or_port(self):
        log = next(r for r in self.rules if r["action"] == "Log")
        self.assertEqual(set(log), {"action", "destination"})
        self.assertEqual(set(log["destination"]), {"notNets"})

    def test_system_namespaces_are_excluded(self):
        sel = self.gnp["spec"]["namespaceSelector"]
        for ns in MUST_EXCLUDE:
            self.assertIn(f"'{ns}'", sel)
        for prefix in MUST_EXCLUDE_PREFIX:
            self.assertIn(f"starts with '{prefix}'", sel)
        self.assertTrue(sel.lstrip().startswith("kubernetes.io/metadata.name not in"))


if __name__ == "__main__":
    unittest.main()
