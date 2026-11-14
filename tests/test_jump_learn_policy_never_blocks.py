"""The jump learn-mode egress policy must never block, and must not log DNS.

infra-zrct (2026-11-04): ns jump is the access path of last resort, so its learn-mode
Calico policy is `Log` then an unconditional `Allow` — a selected endpoint default-denies
every direction in `types`, so losing that trailing Allow cuts the bastion's egress off.

2026-11-06 (infra-kd3t forensics): the Log rule wrote ~9 kernel lines/s on quo (59,625 in
2 h, 96% of the node's journal), 86% of them DNS to kube-dns and 192.168.1.254 —
destinations already known, which the netpol-learn-analyse skill excludes anyway. DNS is
now allowed BEFORE the Log rule; everything else is still logged, then allowed.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

DNS_PORTS = {53}


def egress() -> list[dict]:
    doc = yaml.safe_load((repo_root() / "jump" / "netpol" / "jump-netpol-learn.yml").read_text())
    return doc["spec"]["egress"]


def is_dns_allow(rule: dict) -> bool:
    ports = set((rule.get("destination") or {}).get("ports") or [])
    return rule.get("action") == "Allow" and bool(ports) and ports <= DNS_PORTS


class JumpLearnPolicy(unittest.TestCase):
    def test_last_rule_is_an_unconditional_allow(self):
        last = egress()[-1]
        self.assertEqual(last, {"action": "Allow"})

    def test_exactly_one_log_rule_right_before_the_final_allow(self):
        rules = egress()
        self.assertEqual([r["action"] for r in rules].count("Log"), 1)
        self.assertEqual(rules[-2], {"action": "Log"})

    def test_dns_is_allowed_before_the_log_rule(self):
        rules = egress()
        before_log = rules[: rules.index({"action": "Log"})]
        dns = [r for r in before_log if is_dns_allow(r)]
        self.assertTrue(dns, "no DNS Allow ahead of the Log rule")
        self.assertEqual({r.get("protocol") for r in dns}, {"UDP", "TCP"})
        nets = {n for r in dns for n in (r.get("destination") or {}).get("nets", [])}
        self.assertIn("192.168.1.254/32", nets)
        self.assertTrue(any("kube-dns" in str((r.get("destination") or {}).get("selector", ""))
                            for r in dns), "no kube-dns selector rule")

    def test_nothing_but_dns_skips_the_log(self):
        rules = egress()
        for r in rules[: rules.index({"action": "Log"})]:
            self.assertTrue(is_dns_allow(r), f"non-DNS rule ahead of Log: {r}")


if __name__ == "__main__":
    unittest.main()
