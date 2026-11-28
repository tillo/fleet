"""Apps behind TLS-terminating ingress-nginx set their cookies with the Secure flag.

WHY THIS EXISTS (2026-11-23, infra-7rog.31.12):

The edge-dast ZAP cycle found "Cookie Without Secure Flag" on these hosts. The
backends see plain http from the ingress, so they leave Secure off:
- Paperless's Django `sessionid` and `csrftoken`. Paperless has no setting for
  SESSION_COOKIE_SECURE.
- Roundcube's `roundcube_sessid`.
- MeshCentral's `xid`.
- SFTPGo's `jwt`.
Every one of these hosts is HTTPS-only at the edge. So nginx adds the flag to
whatever the backend sets (`proxy_cookie_flags ~ secure;`), with one line per
Ingress instead of one knob per app, and a knob some apps do not have.
"""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNIPPET = "nginx.ingress.kubernetes.io/configuration-snippet"
FLAG = "proxy_cookie_flags ~ secure;"

# (file, Ingress name): every Ingress serving a host the scan flagged.
INGRESSES = [
    ("paperless/paperless-ing.yml", "paperless"),
    ("paperless-kustodyan/paperless.yml", "paperless-kustodyan"),
    ("meshcentral/meshcentral-ing.yml", "meshcentral"),
    ("spider3/sftpgo-ing.yml", "sftpgo"),
    ("tv/sftpgo-ing.yml", "sftpgo"),
    ("mail/roundcube-ing.yml", "roundcube"),
    ("tillo/tillo-ing.yml", "tillo"),
    ("tillo/pages-ing.yml", "tillo-pages"),
]


def _annotations(path: str, name: str) -> dict:
    for doc in yaml.safe_load_all((ROOT / path).read_text()):
        if doc and doc.get("kind") == "Ingress" and doc["metadata"]["name"] == name:
            return doc["metadata"].get("annotations") or {}
    raise AssertionError(f"no Ingress {name} in {path}")


class AppCookiesAreMarkedSecure(unittest.TestCase):
    def test_every_flagged_ingress_adds_the_secure_flag(self):
        for path, name in INGRESSES:
            with self.subTest(ingress=f"{path}:{name}"):
                snippet = _annotations(path, name).get(SNIPPET, "")
                self.assertIn(FLAG, snippet.splitlines())

    def test_an_existing_snippet_is_kept(self):
        """Roundcube already carries the RUM injection snippet; the flag is added, not swapped in."""
        snippet = _annotations("mail/roundcube-ing.yml", "roundcube")[SNIPPET]
        self.assertIn("sub_filter", snippet)


if __name__ == "__main__":
    unittest.main()
