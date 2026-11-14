"""The Horizon ingress policy admits exactly its measured callers, and logs every drop.

infra-az8.20 (F33): Horizon trusts the client certificate it reads from the
`ssl-client-cert` header. ingress-nginx overwrites that header on the public path, but
inside the cluster any pod could reach horizon.horizon.svc:9000 and send its own. The
learn window (learn.horizon, 2026-11-05 08:25Z -> 2026-11-09 18:50Z, 29,493 ingress
flows, journald suppression 0 on every node) measured the callers; this policy admits
them and denies the rest.

Three ways this goes wrong silently, each pinned below:
  * the learn policy's trailing blanket Allow survives next to the enforcing one and
    defeats it (the flaresolverr promotion lesson) -> the learn file must be gone;
  * a drop with no Log rule in front of it is unanswerable, so the policy gets switched
    off after the first mystery outage -> Log immediately before the final Deny;
  * horizon-discovery is a hostNetwork CronJob pinned by nodeName, so it reaches Horizon
    from that node's flannel.1 address (10.52.<n>.0), not from a pod IP. Moving the
    CronJob to another node without moving the rule cuts discovery off with no error
    in Horizon -> the rule's net must be the pinned node's tunnel address.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

NETPOL = repo_root() / "horizon" / "netpol"
# podCIDR <n> per node; flannel.1 (vxlan) carries the .0 address of the node's /24.
NODE_TUNNEL = {"qui": "10.52.0.0/32", "quo": "10.52.1.0/32", "qua": "10.52.2.0/32",
               "quarto": "10.52.3.0/32"}

# (source namespace, port) of every caller the window measured, plus evertrust-mcp,
# which is declared (HORIZON_URL) but sent nothing in 4.4 days.
CALLERS = {
    ("horizon-issuer", 9000),
    ("kube-system", 9000),     # rke2-ingress-nginx: public horizon.mdapi.ch path
    ("windmill", 9000),
    ("evertrust-mcp", 9000),
    ("monitoring", 9095),
}


def policy() -> dict:
    return yaml.safe_load((NETPOL / "horizon-netpol-enforce.yml").read_text())


def ingress() -> list[dict]:
    return policy()["spec"]["ingress"]


def source_namespace(rule: dict) -> str:
    sel = (rule.get("source") or {}).get("namespaceSelector", "")
    return sel.split('"')[1] if '"' in sel else ""


def discovery_node() -> str:
    docs = yaml.safe_load_all((repo_root() / "horizon-discovery" / "resources.yml").read_text())
    cron = next(d for d in docs if d and d.get("kind") == "CronJob")
    return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["nodeName"]


class HorizonEnforcePolicy(unittest.TestCase):
    def test_learn_policy_is_gone(self):
        self.assertFalse((NETPOL / "horizon-netpol-learn.yml").exists())

    def test_selects_horizon_ingress_only(self):
        spec = policy()["spec"]
        self.assertEqual(spec["selector"], 'app.kubernetes.io/name == "horizon"')
        self.assertEqual(spec["types"], ["Ingress"])

    def test_ends_with_log_then_deny(self):
        self.assertEqual(ingress()[-2:], [{"action": "Log"}, {"action": "Deny"}])

    def test_no_allow_without_a_source(self):
        for r in ingress():
            if r["action"] == "Allow":
                self.assertTrue(r.get("source"), f"unscoped Allow: {r}")

    def test_every_measured_caller_is_admitted_on_its_port(self):
        admitted = {(source_namespace(r), p) for r in ingress() if r["action"] == "Allow"
                    for p in (r.get("destination") or {}).get("ports", [])}
        self.assertEqual(CALLERS - admitted, set())

    def test_discovery_rule_follows_the_cronjob_node(self):
        nets = [n for r in ingress() if r["action"] == "Allow"
                for n in (r.get("source") or {}).get("nets", [])]
        self.assertEqual(nets, [NODE_TUNNEL[discovery_node()]])


if __name__ == "__main__":
    unittest.main()
