"""Tdarr must require a credential, and every node must present one.

infra-o4i3m.4 (2026-11-19): Tdarr ran with auth false, so any pod reaching
tdarr:8265/8266 could edit flows/plugins = code execution on the transcoders.
enforce.tv-apps narrows who reaches it; this is the identity layer behind it.

* Server: auth=true, seededApiKey from Akeyless (tapi_..., created at start if
  absent; with no users it also seeds the `admin` UI user with it as password).
* Every node (the server's internal node and tdarr-node-quarto) sends apiKey,
  or it is refused with "Node socket auth failed".
* Home Assistant's Tdarr integration sends x-api-key; its key lives in HA's
  config entry, not in git.
* The ingress must not carry auth-response-headers: Authorization -- it strips
  the client's header (oauth2-proxy-tv sets none).
"""

from __future__ import annotations

import unittest

from tests.fleetlib import load_docs

SECRET = "tdarr-auth"


def doc(rel: str, kind: str, name: str) -> dict:
    for d in load_docs(rel):
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    raise AssertionError(f"no {kind}/{name} in {rel}")


def env_of(rel: str, name: str) -> dict:
    c = doc(rel, "Deployment", name)["spec"]["template"]["spec"]["containers"][0]
    out = {}
    for e in c.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        out[e["name"]] = (ref["name"], ref["key"]) if ref else e.get("value")
    return out


class TdarrRequiresAuth(unittest.TestCase):
    def test_external_secret_serves_the_api_key_from_akeyless(self):
        es = doc("tv/tdarr-auth-es.yml", "ExternalSecret", SECRET)["spec"]
        self.assertEqual(es["secretStoreRef"]["name"], "cm-akeyless")
        self.assertEqual([(d["remoteRef"]["key"], d["secretKey"]) for d in es["data"]],
                         [("/mdapi/tv/tdarr/api-key", "apiKey")])

    def test_server_enables_auth_and_seeds_the_key(self):
        env = env_of("tv/tdarr-deploy.yml", "tdarr")
        self.assertEqual(env.get("auth"), "true")
        self.assertEqual(env.get("seededApiKey"), (SECRET, "apiKey"))
        self.assertEqual(env.get("apiKey"), (SECRET, "apiKey"), "internal node")

    def test_quarto_node_presents_the_key(self):
        env = env_of("tv/tdarr-node-quarto-deploy.yml", "tdarr-node-quarto")
        self.assertEqual(env.get("apiKey"), (SECRET, "apiKey"))

    def test_ingress_does_not_strip_the_client_authorization_header(self):
        ann = doc("tv/tdarr-ing.yml", "Ingress", "tdarr")["metadata"]["annotations"]
        self.assertNotIn("nginx.ingress.kubernetes.io/auth-response-headers", ann)
        self.assertIn("nginx.ingress.kubernetes.io/auth-url", ann, "SSO must stay in front")


if __name__ == "__main__":
    unittest.main()
