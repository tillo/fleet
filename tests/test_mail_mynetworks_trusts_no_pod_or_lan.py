"""mynetworks trusts no pod and no LAN range (infra-o4i3m.1, step 3).

On 2026-11-10 any pod could relay mail to the internet, unauthenticated, as our own
domains: mynetworks held 10.52.0.0/22 and 192.168.1.0/24, and pods arrive at Postfix
as node IPs (SNAT/hairpin), which sit in that /24. Every in-cluster sender was moved
to 587 + a sender login first (infra-o4i3m.1.1-.1.3). What remains trusted by
address is localhost and the one device that cannot log in: the HP M475dn printer
on 2525 (TLS 1.0 only, infra-hnbe.5). Since externalTrafficPolicy Local (fleet
!2366) it reaches Postfix as its own address.

A broader entry here re-opens the relay for every pod, silently: mail still flows.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

VALUES = repo_root() / "mail" / "docker-mailserver" / "values.yml"
EXPECTED = ["127.0.0.0/8", "[::1]/128", "192.168.1.11/32"]


def mynetworks() -> list[str]:
    cf = yaml.safe_load(VALUES.read_text())["configMaps"]["postfix-main.cf"]["data"]
    line = next(l for l in cf.splitlines() if l.startswith("mynetworks ="))
    return line.split("=", 1)[1].split()


class MynetworksTrustsNoPodOrLan(unittest.TestCase):
    def test_only_localhost_and_the_printer(self):
        self.assertEqual(mynetworks(), EXPECTED)

    def test_every_entry_is_a_single_host_except_loopback(self):
        for net in mynetworks():
            if net.startswith(("127.", "[::1]")):
                continue
            self.assertTrue(net.endswith("/32"), net)


if __name__ == "__main__":
    unittest.main()
