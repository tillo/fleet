"""The beads Dolt server requires TLS (infra-7rog.31.21, user decision 2026-11-23).

Edge DAST (testssl) flagged 192.168.1.63:3306; by hand `openssl s_client -starttls mysql`
got "MySQL server does not support SSL". The LoadBalancer serves the Mac seat (bd +
Beadazzle), so every login and every row of the tracker crossed the LAN in clear.

The fix, and what would silently undo it:
  * dolt-sql-server only reads a server config from /etc/dolt/servercfg.d/*.yaml (exactly
    one file; with two the entrypoint warns and falls back to defaults -- no TLS);
  * the config must keep listening on 0.0.0.0:3306 with data_dir /var/lib/dolt, or the
    server starts empty / unreachable;
  * require_secure_transport must be TRUE: a cert alone only OFFERS TLS, and a client
    that never asks for it still logs in in clear;
  * the cert comes from the private CA (horizon-private) with every name a client dials
    (the LB IP, the Service names), and the pod mounts it from the Secret cert-manager writes;
  * in-cluster bd (backlog-app) must ask for TLS too, or `require_secure_transport` locks it out.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from tests.fleetlib import repo_root

ROOT = Path(repo_root()) / "backlog"


def docs(name):
    return [d for d in yaml.safe_load_all((ROOT / name).read_text()) if d]


def one(name, kind, meta_name):
    (d,) = [d for d in docs(name) if d["kind"] == kind and d["metadata"]["name"] == meta_name]
    return d


class DoltRequiresTls(unittest.TestCase):
    def setUp(self):
        self.sts = one("dolt-sts.yml", "StatefulSet", "dolt")
        self.pod = self.sts["spec"]["template"]["spec"]
        (self.c,) = self.pod["containers"]
        self.mounts = {m["mountPath"]: m for m in self.c["volumeMounts"]}
        self.vols = {v["name"]: v for v in self.pod["volumes"]}
        self.cm = one("dolt-tls.yml", "ConfigMap", "dolt-servercfg")

    def test_exactly_one_server_config_file_is_mounted_where_the_entrypoint_reads(self):
        m = self.mounts["/etc/dolt/servercfg.d"]
        self.assertEqual(self.vols[m["name"]]["configMap"]["name"], "dolt-servercfg")
        self.assertEqual(len([k for k in self.cm["data"] if k.endswith(".yaml")]), 1)

    def test_the_config_keeps_the_listener_and_data_dir_and_serves_tls(self):
        (cfg,) = [yaml.safe_load(v) for k, v in self.cm["data"].items() if k.endswith(".yaml")]
        listener = cfg["listener"]
        self.assertEqual((listener["host"], listener["port"]), ("0.0.0.0", 3306))
        self.assertEqual(cfg["data_dir"], "/var/lib/dolt")
        tls_dir = next(p for p, m in self.mounts.items() if self.vols.get(m["name"], {}).get("secret"))
        self.assertEqual(listener["tls_cert"], f"{tls_dir}/tls.crt")
        self.assertEqual(listener["tls_key"], f"{tls_dir}/tls.key")

    def test_the_dast_server_name_is_on_the_certificate(self):
        # Edge DAST verifies the cert against this name; one absent from the SANs is a
        # false HIGH "does not match" every night.
        lb = one("dolt-svc.yml", "Service", "dolt-lb")["metadata"]["annotations"]["dast.mdapi.ch/server-name"]
        self.assertIn(lb, one("dolt-tls.yml", "Certificate", "dolt-tls")["spec"]["dnsNames"])

    def test_tls_is_required_not_merely_offered(self):
        (cfg,) = [yaml.safe_load(v) for k, v in self.cm["data"].items() if k.endswith(".yaml")]
        self.assertIs(cfg["listener"]["require_secure_transport"], True)

    def test_the_cert_is_private_ca_and_names_every_address_a_client_dials(self):
        cert = one("dolt-tls.yml", "Certificate", "dolt-tls")["spec"]
        self.assertEqual(cert["issuerRef"], {"group": "horizon.evertrust.io", "kind": "ClusterIssuer",
                                             "name": "horizon-private"})
        lb = one("dolt-svc.yml", "Service", "dolt-lb")["spec"]["loadBalancerIP"]
        self.assertIn(lb, cert["ipAddresses"])
        # The LAN dials a NAME: the private-tls profile issues no IP SANs, and a client
        # that dials 192.168.1.63 gets "certificate doesn't contain any IP SANs".
        self.assertIn("dolt.home.tillo.ch", cert["dnsNames"])
        for name in ("dolt", "dolt.backlog.svc", "dolt.backlog.svc.cluster.local"):
            self.assertIn(name, cert["dnsNames"])
        secret = next(v["secret"]["secretName"] for v in self.vols.values() if v.get("secret"))
        self.assertEqual(cert["secretName"], secret)

    def test_the_in_cluster_bd_asks_for_tls(self):
        meta = json.loads(one("app-cm.yml", "ConfigMap", "beads-workspace")["data"]["metadata.json"])
        self.assertIs(meta.get("dolt_server_tls"), True)

    def test_every_bd_container_trusts_the_private_ca(self):
        # bd is Go: it verifies against SSL_CERT_FILE. The node image's own store lacks the
        # MDAPI Root CA, so each container that runs bd reads trust-manager's bundle.
        app = one("app.yml", "Deployment", "beads-app")["spec"]["template"]["spec"]
        vols = {v["name"]: v for v in app["volumes"]}
        for c in app["initContainers"] + app["containers"]:
            with self.subTest(container=c["name"]):
                env = {e["name"]: e.get("value") for e in c.get("env", [])}
                path = env.get("SSL_CERT_FILE")
                self.assertTrue(path, "SSL_CERT_FILE unset")
                (m,) = [m for m in c["volumeMounts"] if path.startswith(m["mountPath"] + "/")]
                self.assertEqual(vols[m["name"]]["configMap"]["name"], "mdapi-trust-bundle")


if __name__ == "__main__":
    unittest.main()
