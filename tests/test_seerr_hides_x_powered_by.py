"""seerr.mdapi.ch must not advertise `X-Powered-By: Express`.

WHY THIS EXISTS (2026-11-23, infra-7rog.31.12):

The edge-dast ZAP cycle flagged "Server Leaks Information via X-Powered-By" on
seerr. Express sends the header on every response and seerr exposes no setting
to disable it, so the edge drops it with `proxy_hide_header`.

Roundcube's X-Powered-By was a different fix: PHP's `expose_php = Off` in the
image's own overrides (mail/roundcube-cm.yml).
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNIPPET = "nginx.ingress.kubernetes.io/configuration-snippet"


class SeerrHidesXPoweredBy(unittest.TestCase):
    def test_the_seerr_ingress_hides_the_header(self):
        ing = yaml.safe_load((ROOT / "tv/seerr-ing.yml").read_text())
        snippet = ing["metadata"]["annotations"][SNIPPET]
        self.assertIn("proxy_hide_header X-Powered-By;", snippet.splitlines())


if __name__ == "__main__":
    unittest.main()
