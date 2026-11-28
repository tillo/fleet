"""Every Host paperless is asked under must be in its ALLOWED_HOSTS.

The incident this encodes (O2 SyntheticProbeFailing on https://documents.mdapi.ch,
infra-tjscj, 2026-11-17): Renovate bumped paperless-ngx to v3.5.0 (fleet 1071b3d2).
Starting with that version, setting PAPERLESS_URL no longer leaves ALLOWED_HOSTS at "*".
It becomes PAPERLESS_ALLOWED_HOSTS (empty when unset) + "localhost" + the hostname of
PAPERLESS_URL (src/paperless/settings/__init__.py `_get_allowed_hosts`). The kubelet's
httpGet probes send `Host: <pod IP>:8000`, so Django answered 400 DisallowedHost. The
startupProbe never passed, the container was restarted every 10 min (26 times), and the
site was down for ~16 h. In-cluster callers (paperless-mcp, the f/paperless Windmill
scripts) use the Service FQDN and would have been refused too.
"""

import unittest
from urllib.parse import urlparse

import fleetlib
from test_paperless_liveness_cannot_kill_a_booting_pod import paperless_container

PROBES = ("startupProbe", "readinessProbe", "livenessProbe")
# Grep the repo for http://paperless.paperless...: paperless-mcp-deploy.yml and the
# f/paperless Windmill scripts call the Service by its FQDN.
IN_CLUSTER_CALLER_HOSTS = ("paperless.paperless.svc.cluster.local",)
POD_IP_SENTINEL = "<pod-ip>"


def env(container: dict) -> dict:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def allowed_hosts(e: dict) -> list:
    """paperless-ngx >= 3.5.0 rule, `_get_allowed_hosts` + `_parse_paperless_url`."""
    if "PAPERLESS_ALLOWED_HOSTS" in e or e.get("PAPERLESS_URL"):
        hosts = [h.strip() for h in (e.get("PAPERLESS_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    else:
        hosts = ["*"]
    if hosts != ["*"]:
        hosts.append("localhost")
    if e.get("PAPERLESS_URL"):
        hosts.append(urlparse(e["PAPERLESS_URL"]).hostname)
    return hosts


def probe_host(probe: dict) -> str:
    for h in probe.get("httpGet", {}).get("httpHeaders", []):
        if h["name"].lower() == "host":
            return h["value"].split(":")[0]
    return POD_IP_SENTINEL  # kubelet default: the pod IP


def admits(hosts: list, host: str) -> bool:
    return "*" in hosts or host in hosts


class PaperlessAllowedHosts(unittest.TestCase):
    def setUp(self):
        self.c = paperless_container()
        self.hosts = allowed_hosts(env(self.c))

    def test_the_rule_model_matches_the_incident(self):
        # The pre-fix manifest: PAPERLESS_URL only. The pod IP must be refused.
        hosts = allowed_hosts({"PAPERLESS_URL": "https://documents.mdapi.ch"})
        self.assertEqual(hosts, ["localhost", "documents.mdapi.ch"])
        self.assertFalse(admits(hosts, POD_IP_SENTINEL))

    def test_kubelet_probes_are_admitted(self):
        for kind in PROBES:
            with self.subTest(kind=kind):
                self.assertTrue(admits(self.hosts, probe_host(self.c[kind])),
                                f"{kind} Host {probe_host(self.c[kind])!r} not in {self.hosts}")

    def test_in_cluster_callers_are_admitted(self):
        for host in IN_CLUSTER_CALLER_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(admits(self.hosts, host), f"{host!r} not in {self.hosts}")

    def test_public_host_is_admitted_and_wildcard_is_not_used(self):
        self.assertTrue(admits(self.hosts, "documents.mdapi.ch"))
        self.assertNotIn("*", self.hosts, "a wildcard re-opens Host-header poisoning")


if __name__ == "__main__":
    unittest.main()
