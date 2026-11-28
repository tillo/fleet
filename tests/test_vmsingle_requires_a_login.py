"""vmsingle requires a login on every path, and only vmauth holds it (infra-o4i3m.5.4).

Before this, any pod could write, import, delete-by-overwrite or read any series on
vmsingle-mdapi:8428/8429. Every client now goes through vmauth-vmsingle with its own
scoped user; vmsingle itself takes exactly one Basic login (user `vmauth`, Secret
vmsingle-httpauth), the one vmauth sends on every route (targetRefBasicAuth).

Pinned here:
  * -httpAuth.username/password are set, the password READ FROM THE MOUNTED SECRET
    (file://), never a literal, and that Secret is mounted.
  * The operator's self-scrape of vmsingle /metrics logs in with the same Secret:
    without -metricsAuthKey, /metrics falls back to the Basic login, so an unauthenticated
    scrape would turn vmsingle's own metrics (and every alert on them) into 401s.
  * -metricsAuthKey stays unset: the operator copies its VALUE into the scrape params in
    plain text, which would put a credential in a generated object.
  * Probes need no change: VictoriaMetrics answers /health before any auth check
    (lib/httpserver handlerWrapper, v1.155.0).
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

VMSINGLE = repo_root() / "monitoring-vm" / "vmsingle.yml"
SECRET = "vmsingle-httpauth"


def spec() -> dict:
    return yaml.safe_load(VMSINGLE.read_text())["spec"]


class VmsingleRequiresALogin(unittest.TestCase):
    def test_http_auth_user_is_vmauth(self):
        self.assertEqual(spec()["extraArgs"]["httpAuth.username"], "vmauth")

    def test_password_is_read_from_the_mounted_secret(self):
        self.assertEqual(spec()["extraArgs"]["httpAuth.password"],
                         f"file:///etc/vm/secrets/{SECRET}/password")
        self.assertIn(SECRET, spec()["secrets"])

    def test_admin_authkey_secret_is_still_mounted(self):
        self.assertIn("vmsingle-admin-authkey", spec()["secrets"])

    def test_self_scrape_logs_in_with_the_same_secret(self):
        eps = spec()["serviceScrapeSpec"]["endpoints"]
        http = [e for e in eps if e["port"] == "http"]
        self.assertEqual(len(http), 1)
        self.assertEqual(http[0]["path"], "/metrics")
        self.assertEqual(http[0]["basicAuth"], {
            "username": {"name": SECRET, "key": "username"},
            "password": {"name": SECRET, "key": "password"},
        })

    def test_metrics_auth_key_stays_unset(self):
        self.assertNotIn("metricsAuthKey", spec()["extraArgs"])


if __name__ == "__main__":
    unittest.main()
