"""HA and MeshCentral admit only the callers the learn window proved, and deny the rest.

infra-o4i3m.10 (2026-11-16): Home Assistant (trusted_proxies) and MeshCentral
(trustedProxy) honour X-Forwarded-For from the whole pod CIDR, because their only real
proxy, rke2-ingress-nginx, has churning pod IPs inside it. So any pod could connect
directly and choose the client IP they log and ip_ban. Narrowing the trust list cannot
fix that; limiting who may CONNECT does.

learn.home-assistant and learn.meshcentral recorded every caller other than ingress-nginx
from 2026-11-17 19:56Z for ~49 h (journald suppression 0 on every node). Attributed by
pod IP at the time of each flow (kube_pod_info), the only callers were:
  * HA: Windmill workers (default + heal groups: zha_mesh_health, ha_stream_unwedge,
    ha_upgrade_rollback, f/lib/mdapi), vmagent-mdapi (scrape), and the jump pod's own
    probes when the learn policies went live.
  * MeshCentral: the jump pod's probes only.
Analysis: ~/ha-meshcentral-netpol-analysis-20261119.md.

Pinned here:
  * The learn policies are gone (their trailing Allow would defeat enforcement).
  * Ingress only; each ends Log then Deny, and no rule allows unconditionally.
  * ingress-nginx/Traefik stay allowed for both (dual list, as tv).
  * HA additionally admits Windmill workers and vmagent, on 8123 only; MeshCentral
    admits nothing else. The jump pod is NOT admitted (it uses the ingress like a user).
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

INGRESS_CONTROLLERS = {
    "action": "Allow", "protocol": "TCP",
    "source": {"namespaceSelector": 'kubernetes.io/metadata.name == "kube-system"',
               "selector": 'app.kubernetes.io/name in {"rke2-ingress-nginx", "rke2-traefik"}'},
}
LOG_DENY = [{"action": "Log"}, {"action": "Deny"}]

# file -> (name, namespace, selector, exact ingress rules)
POLICIES = {
    "home-assistant/home-assistant-netpol-enforce.yml": (
        "enforce.home-assistant", "home-assistant", 'app == "home-assistant"',
        [INGRESS_CONTROLLERS,
         {"action": "Allow", "protocol": "TCP",
          "source": {"namespaceSelector": 'kubernetes.io/metadata.name == "windmill"',
                     "selector": 'app == "windmill-workers"'},
          "destination": {"ports": [8123]}},
         {"action": "Allow", "protocol": "TCP",
          "source": {"namespaceSelector": 'kubernetes.io/metadata.name == "monitoring"',
                     "selector": 'app.kubernetes.io/name == "vmagent"'},
          "destination": {"ports": [8123]}},
         *LOG_DENY],
    ),
    "meshcentral/meshcentral-netpol-enforce.yml": (
        "enforce.meshcentral", "meshcentral", 'app == "meshcentral"',
        [INGRESS_CONTROLLERS, *LOG_DENY],
    ),
}
LEARN = ["home-assistant/home-assistant-netpol-learn.yml",
         "meshcentral/meshcentral-netpol-learn.yml"]


def load(path: str) -> dict:
    return yaml.safe_load((repo_root() / path).read_text())


class O4i3m10EnforcePolicies(unittest.TestCase):
    def test_learn_policies_are_gone(self):
        for path in LEARN:
            with self.subTest(path=path):
                self.assertFalse((repo_root() / path).exists())

    def test_each_policy_admits_exactly_the_proven_callers(self):
        for path, (name, ns, selector, rules) in POLICIES.items():
            with self.subTest(path=path):
                doc = load(path)
                self.assertEqual(doc["apiVersion"], "crd.projectcalico.org/v1")
                self.assertEqual(doc["kind"], "NetworkPolicy")
                self.assertEqual(doc["metadata"], {"name": name, "namespace": ns})
                s = doc["spec"]
                self.assertEqual(s["order"], 500)
                self.assertEqual(s["selector"], selector)
                self.assertEqual(s["types"], ["Ingress"])
                self.assertEqual(s["ingress"], rules)
                self.assertNotIn({"action": "Allow"}, s["ingress"])


if __name__ == "__main__":
    unittest.main()
