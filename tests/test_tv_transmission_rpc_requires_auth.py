"""Transmission RPC must require a credential, and every caller must send one.

infra-o4i3m.4 (2026-11-19): Transmission ran with rpc-authentication-required
false, so any pod reaching transmission:9091 could call session-set and point
script-torrent-done-filename at a script = code execution in the container. The
tv NetworkPolicy (enforce.tv-apps) narrows WHO can reach it; this is the identity
layer behind it.

The linuxserver image turns RPC auth on when USER and PASS are both set (its
init-transmission-config rewrites settings.json on every start), so the
credential comes from Akeyless via ESO and never sits in git. Three callers
must carry it, or they fail:

* the port-manager sidecar (127.0.0.1:9091 -- localhost is NOT exempt, the
  whitelist is off): it reads TR_AUTH and passes -ne to transmission-remote;
* sonarr and radarr download clients: credentials live in the apps' own DBs,
  set through their API, not in git;
* the browser through the ingress: the boilerplate
  auth-response-headers: Authorization annotation would strip the browser's
  Basic header (oauth2-proxy-tv sets none), so it must not be on this ingress.
"""

from __future__ import annotations

import re
import unittest

from tests.fleetlib import load_docs

SECRET = "transmission-rpc"
DEPLOY = "tv/transmission-pia-deploy.yml"


def doc(rel: str, kind: str, name: str) -> dict:
    for d in load_docs(rel):
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    raise AssertionError(f"no {kind}/{name} in {rel}")


def container(name: str) -> dict:
    spec = doc(DEPLOY, "Deployment", "transmission-pia")["spec"]["template"]["spec"]
    return next(c for c in spec["containers"] if c["name"] == name)


def secret_env(c: dict) -> dict[str, tuple[str, str]]:
    out = {}
    for e in c.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        if ref:
            out[e["name"]] = (ref["name"], ref["key"])
    return out


class TransmissionRpcRequiresAuth(unittest.TestCase):
    def test_external_secret_serves_user_pass_and_tr_auth_from_akeyless(self):
        es = doc("tv/transmission-rpc-es.yml", "ExternalSecret", SECRET)["spec"]
        self.assertEqual(es["secretStoreRef"]["name"], "cm-akeyless")
        keys = {d["remoteRef"]["key"] for d in es["data"]}
        self.assertEqual(keys, {"/mdapi/tv/transmission/rpc-username",
                                "/mdapi/tv/transmission/rpc-password"})
        tpl = es["target"]["template"]["data"]
        self.assertEqual(set(tpl), {"USER", "PASS", "TR_AUTH"})
        self.assertEqual(tpl["TR_AUTH"], "{{ .username }}:{{ .password }}")

    def test_transmission_gets_user_and_pass_so_the_image_enables_rpc_auth(self):
        env = secret_env(container("transmission"))
        self.assertEqual(env.get("USER"), (SECRET, "USER"))
        self.assertEqual(env.get("PASS"), (SECRET, "PASS"))

    def test_port_manager_authenticates_every_rpc_call(self):
        self.assertEqual(secret_env(container("port-manager")).get("TR_AUTH"),
                         (SECRET, "TR_AUTH"))
        script = doc("tv/transmission-port-manager-cm.yml", "ConfigMap",
                     "transmission-port-manager")["data"]["manage.sh"]
        calls = [line for line in script.splitlines()
                 if re.search(r"(^|[;&|!]|\bif|\bthen|\$\()\s*transmission-remote\b", line)]
        self.assertTrue(calls, "port-manager no longer calls transmission-remote?")
        for line in calls:
            self.assertRegex(line, r"\s-ne\b", f"unauthenticated RPC call: {line.strip()}")

    def test_ingress_does_not_strip_the_browser_basic_header(self):
        ann = doc("tv/transmission-ing.yml", "Ingress", "transmission")["metadata"]["annotations"]
        self.assertNotIn("nginx.ingress.kubernetes.io/auth-response-headers", ann)
        self.assertIn("nginx.ingress.kubernetes.io/auth-url", ann, "SSO must stay in front")


if __name__ == "__main__":
    unittest.main()
