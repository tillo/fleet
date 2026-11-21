"""Only bpi-r4 may speak PROXY protocol to docker-mailserver (infra-o4i3m.2, .1 step 1).

The PROXY ports exist so that the edge haproxy on bpi-r4 can tell Postfix/Dovecot the
real client address. Whoever reaches such a port can claim any client address. The
audit of 2026-11-10 proved it from the jump pod: `PROXY TCP4 127.0.0.1 ...` on 12525
followed by RCPT to an external address got 250, so any pod could relay as localhost.

Neither mynetworks nor a policy could tell haproxy from a pod while the Service ran
`externalTrafficPolicy: Cluster`, because the router arrived SNATed to a node IP,
exactly like a pod that hairpins through the LoadBalancer. `Local` keeps the router's
own address (192.168.1.254), so the policy can admit it by address.

Three ways this goes wrong, each pinned below:
  * the Service goes back to `Cluster`: the router then arrives as a node IP, the
    Allow matches nothing and ALL public mail stops (inbound MX and submission);
  * a PROXY port is added in values.yml but not here: it stays open to every pod;
  * the policy denies more than the PROXY ports: mail is priority 1, so every other
    port must fall through to the final Allow, unchanged.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

MAIL = repo_root() / "mail"
VALUES = MAIL / "docker-mailserver" / "values.yml"
ROUTER = "192.168.1.254/32"
# Postfix 12525 (postscreen, MX), 10465/10587 (submission); Dovecot 10143/10993 (IMAP),
# 14190 (ManageSieve). bpi-r4 haproxy forwards to all but 10143.
PROXY_PORTS = [10143, 10465, 10587, 10993, 12525, 14190]


def policy() -> dict:
    return yaml.safe_load((MAIL / "docker-mailserver-netpol.yml").read_text())


def ingress() -> list[dict]:
    return policy()["spec"]["ingress"]


def values() -> dict:
    return yaml.safe_load(VALUES.read_text())


def ports(rule: dict) -> list[int]:
    return sorted((rule.get("destination") or {}).get("ports", []))


class MailProxyPortsAdmitOnlyTheRouter(unittest.TestCase):
    def test_selects_the_mail_server_ingress_only(self):
        spec = policy()["spec"]
        self.assertEqual(spec["selector"], 'app.kubernetes.io/name == "docker-mailserver"')
        self.assertEqual(spec["types"], ["Ingress"])

    def test_router_alone_is_admitted_on_the_proxy_ports(self):
        first = ingress()[0]
        self.assertEqual(first["action"], "Allow")
        self.assertEqual(first["source"], {"nets": [ROUTER]})
        self.assertEqual(ports(first), PROXY_PORTS)

    def test_everyone_else_is_logged_then_denied_on_the_proxy_ports(self):
        log, deny = ingress()[1:3]
        self.assertEqual([log["action"], deny["action"]], ["Log", "Deny"])
        for rule in (log, deny):
            self.assertNotIn("source", rule)
            self.assertEqual(ports(rule), PROXY_PORTS)

    def test_every_other_port_falls_through_unchanged(self):
        self.assertEqual(ingress()[3:], [{"action": "Allow"}])

    def test_service_keeps_the_router_address(self):
        self.assertEqual(values()["service"]["externalTrafficPolicy"], "Local")

    def test_dovecot_trusts_proxy_headers_from_the_router_only(self):
        self.assertEqual(values()["proxyProtocol"]["trustedNetworks"], ROUTER)

    def test_every_proxy_port_is_a_listener_in_values(self):
        text = VALUES.read_text()
        for port in PROXY_PORTS:
            self.assertIn(str(port), text)


if __name__ == "__main__":
    unittest.main()
