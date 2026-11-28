"""grafana.mdapi.ch signs the SSO user into Main Org; anonymous stays in Embed.

WHY THIS EXISTS (2026-11-20):

infra-o4i3m.6 moved anonymous Viewer to org 2 "Embed" (VM-only), because Grafana
OSS has no per-datasource permissions. grafana.mdapi.ch sits behind
oauth2-proxy-admin, but Grafana was never told who the user is, so an SSO user
landed in Embed too and saw 10 of 75 dashboards ("Grafana lost its dashboards").

The fix is Grafana's own OIDC login (generic_oauth, Keycloak client `grafana`,
kc-gate role mdapi-admin), NOT header trust (auth.proxy): ingress-nginx runs on
pod IPs, so auth.proxy's whitelist cannot tell the ingress from any other pod,
and grafana-embed forwards client headers ungated.
"""

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent / "monitoring-grafana"


def _env() -> dict:
    deploy = yaml.safe_load((ROOT / "grafana-deploy.yml").read_text())
    (c,) = [c for c in deploy["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana"]
    return {e["name"]: e for e in c["env"]}


def _ingress(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())["metadata"]["annotations"]


class GrafanaSsoLogin(unittest.TestCase):
    def test_anonymous_stays_in_embed_org(self):
        env = _env()
        self.assertEqual(env["GF_AUTH_ANONYMOUS_ORG_NAME"]["value"], "Embed")
        self.assertEqual(env["GF_AUTH_ANONYMOUS_ORG_ROLE"]["value"], "Viewer")

    def test_generic_oauth_is_keycloak_with_strict_role_mapping(self):
        env = _env()
        self.assertEqual(env["GF_AUTH_GENERIC_OAUTH_ENABLED"]["value"], "true")
        self.assertEqual(env["GF_AUTH_GENERIC_OAUTH_CLIENT_ID"]["value"], "grafana")
        for k in ("AUTH_URL", "TOKEN_URL", "API_URL"):
            self.assertTrue(env[f"GF_AUTH_GENERIC_OAUTH_{k}"]["value"].startswith(
                "https://idp.mdapi.ch/realms/tilloch/protocol/openid-connect/"), k)
        # A user without mdapi-admin must be refused, never defaulted to Viewer in Main Org.
        self.assertEqual(env["GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT"]["value"], "true")
        self.assertIn("mdapi-admin", env["GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH"]["value"])

    def test_no_header_trust(self):
        self.assertNotIn("GF_AUTH_PROXY_ENABLED", _env())

    def test_client_secret_comes_from_akeyless_only(self):
        self.assertNotIn("GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET", _env())
        es = yaml.safe_load((ROOT / "grafana-es.yml").read_text())
        keys = {d["secretKey"]: d["remoteRef"]["key"] for d in es["spec"]["data"]}
        self.assertEqual(keys.get("GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET"), "/mdapi/grafana/oauth-client-secret")

    def test_sso_host_sends_sessionless_browsers_to_login(self):
        snip = _ingress("grafana-ing.yml").get("nginx.ingress.kubernetes.io/configuration-snippet", "")
        self.assertIn("$cookie_grafana_session", snip)
        self.assertIn("/login?redirectTo=", snip)
        # /login itself (and the OAuth callback under it) must be exempt, or it loops.
        exempt = re.search(r'\$uri ~ "([^"]+)"', snip)
        self.assertIsNotNone(exempt)
        for path in ("/login", "/login/generic_oauth", "/public/build/app.js", "/api/health"):
            self.assertRegex(path, exempt.group(1), path)
        self.assertNotRegex("/d/mdapi-overview/x", exempt.group(1))

    def test_embed_host_never_redirects_to_login(self):
        # Kiosks and the Ceph iframes are anonymous by design.
        # Its only snippet is the X-Frame-Options opt-out (test_edge_security_headers).
        snip = _ingress("grafana-embed-ing.yml").get("nginx.ingress.kubernetes.io/configuration-snippet", "")
        self.assertNotIn("return", snip)
        self.assertNotIn("/login", snip)
        self.assertEqual('set $edge_frame_guard "off";', snip.strip())


if __name__ == "__main__":
    unittest.main()
