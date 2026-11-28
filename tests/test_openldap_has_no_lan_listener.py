"""OpenLDAP is reachable from inside the cluster only (infra-7rog.31.21).

slapd runs with ENABLE_TLS=false: binds and every userPassword-bearing search cross the
wire in clear. Inside the cluster that wire is the pod network; a LoadBalancer put it on
the LAN (192.168.1.52, added 2026-04-20 for VPN auth). The VPN stopped using LDAP when it
moved into the cluster, and on 2026-11-23 every live client (mail, Keycloak x2) and every
configured one (LAM, Joplin, Roundcube, Keycloak's federation) used
openldap.openldap.svc.cluster.local -- so the LAN listener only exposed cleartext binds.

If a LAN client ever needs LDAP again, enable TLS first and only then expose it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from tests.fleetlib import repo_root

SVC = Path(repo_root()) / "openldap" / "openldap-svc.yml"


class OpenLdapStaysInCluster(unittest.TestCase):
    def test_the_service_is_not_exposed_outside_the_cluster(self):
        (svc,) = [d for d in yaml.safe_load_all(SVC.read_text()) if d]
        spec = svc["spec"]
        self.assertEqual(spec.get("type", "ClusterIP"), "ClusterIP")
        for key in ("loadBalancerIP", "loadBalancerClass", "externalTrafficPolicy", "externalIPs"):
            self.assertNotIn(key, spec)

    def test_the_in_cluster_name_and_port_are_kept(self):
        # Every client dials ldap://openldap.openldap.svc.cluster.local:389.
        (svc,) = [d for d in yaml.safe_load_all(SVC.read_text()) if d]
        self.assertEqual((svc["metadata"]["name"], svc["metadata"]["namespace"]), ("openldap", "openldap"))
        self.assertEqual([p["port"] for p in svc["spec"]["ports"]], [389])
        self.assertEqual(svc["spec"]["selector"], {"app": "openldap"})


if __name__ == "__main__":
    unittest.main()
