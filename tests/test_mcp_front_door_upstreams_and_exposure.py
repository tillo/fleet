"""The MCP front door (agentgateway, mcp.mdapi.ch): every upstream bearer is mounted, and
only the OAuth paths are reachable from outside the LAN.

1. Upstream bearers. The database-held MCP routes reference each upstream's credential as
   the FILE /mcp-upstream/<server> (backendAuth.key.value.file). An ExternalSecret
   `mcp-upstream-<server>` in agentgateway-standalone-wiring that is not projected into
   that volume in agentgateway-standalone/fleet.yaml syncs fine, the bundle is Ready, and
   the route sends no credential: the upstream answers 401 to every client while the
   gateway looks healthy. The reverse (a projected Secret nobody creates) is only safe as
   an `optional` source, otherwise the pods never start.

2. Exposure. mcp.mdapi.ch carries the API-key routes (CLI seats, kagent) AND, since
   infra-az8.49/az8.46, Keycloak-OAuth routes for the claude.ai connectors under /oauth/.
   The claude.ai connector egress must reach the OAuth routes and the
   /.well-known/oauth-* discovery documents, nothing else. A public source range on the
   main Ingress would put every API-key route on the Internet; so the public range lives
   on a separate Ingress whose paths are exactly the OAuth prefixes.
"""

import ipaddress
import unittest

import fleetlib

WIRING = "agentgateway-standalone-wiring"
HELM_BUNDLE = "agentgateway-standalone/fleet.yaml"
SOURCE_RANGE = "nginx.ingress.kubernetes.io/whitelist-source-range"
OAUTH_PATHS = {"/oauth/", "/.well-known/oauth-protected-resource/oauth/",
               "/.well-known/oauth-authorization-server/oauth/"}
LAN = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "10.0.0.0/8")]


def wiring_docs(kind: str) -> list[dict]:
    return [d for f in fleetlib.files_under(WIRING) for d in fleetlib.load_docs(f)
            if d.get("kind") == kind]


def upstream_external_secrets() -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in wiring_docs("ExternalSecret")
            if d["metadata"]["name"].startswith("mcp-upstream-")}


def projected_sources() -> list[dict]:
    values = fleetlib.bundle_spec(HELM_BUNDLE)["helm"]["values"]
    vols = [v for v in values["extraVolumes"] if v["name"] == "mcp-upstream"]
    return [s["secret"] for s in vols[0]["projected"]["sources"]]


def mcp_ingresses() -> list[dict]:
    return [d for d in wiring_docs("Ingress")
            if any(r.get("host") == "mcp.mdapi.ch" for r in d["spec"]["rules"])]


def is_public(cidr: str) -> bool:
    net = ipaddress.ip_network(cidr.strip())
    return not any(net.subnet_of(lan) for lan in LAN)


def paths(ing: dict) -> set[str]:
    return {p["path"] for r in ing["spec"]["rules"] for p in r["http"]["paths"]}


class UpstreamBearersAreMounted(unittest.TestCase):
    def test_every_upstream_secret_is_projected_at_its_server_path(self):
        es = upstream_external_secrets()
        self.assertTrue(es, "measured NOTHING: no mcp-upstream-* ExternalSecret found")
        projected = {s["name"]: s for s in projected_sources()}
        for name, doc in es.items():
            server = name.removeprefix("mcp-upstream-")
            with self.subTest(server=server):
                self.assertEqual(doc["spec"]["target"]["name"], name)
                self.assertIn(name, projected, f"{name} is synced but never mounted")
                self.assertEqual(projected[name]["items"],
                                 [{"key": "token", "path": server}])

    def test_every_projected_secret_has_a_source_or_is_optional(self):
        es = upstream_external_secrets()
        for src in projected_sources():
            with self.subTest(secret=src["name"]):
                self.assertTrue(src["name"] in es or src.get("optional") is True)


class OnlyOAuthPathsArePublic(unittest.TestCase):
    def test_public_source_ranges_only_on_the_oauth_ingress(self):
        ings = mcp_ingresses()
        self.assertGreaterEqual(len(ings), 2, "measured NOTHING: expected main + oauth")
        public = [i for i in ings
                  if any(is_public(c) for c in
                         i["metadata"]["annotations"][SOURCE_RANGE].split(","))]
        self.assertEqual(len(public), 1, "exactly one mcp.mdapi.ch Ingress may be public")
        self.assertEqual(paths(public[0]), OAUTH_PATHS)

    def test_every_mcp_ingress_is_source_restricted(self):
        for ing in mcp_ingresses():
            with self.subTest(ingress=ing["metadata"]["name"]):
                self.assertTrue(ing["metadata"]["annotations"].get(SOURCE_RANGE))

    def test_lan_check_classifies_ranges(self):
        self.assertFalse(is_public("192.168.1.0/24"))
        self.assertFalse(is_public("192.168.164.3/32"))
        self.assertTrue(is_public("160.79.106.0/24"))


if __name__ == "__main__":
    unittest.main()
