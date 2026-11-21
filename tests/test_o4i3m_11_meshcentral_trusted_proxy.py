"""MeshCentral trusts X-Forwarded-For only from the pod CIDR, not from every private range.

infra-o4i3m.11 (2026-11-20), the tidy-up after infra-o4i3m.10: the enforce policy
(meshcentral-netpol-enforce.yml) is what closed the forged-XFF hole, since only the
ingress controllers may connect. This narrows the trust list to where those controllers
actually live (cluster pod CIDR 10.52.0.0/16), so the LAN, 172.16/12 and the rest of 10/8
can no longer choose the client IP MeshCentral logs. Home Assistant got the same change
through its own http config store (not in git).
"""

from __future__ import annotations

import json
import unittest

import yaml

from tests.fleetlib import repo_root

POD_CIDR = "10.52.0.0/16"


def meshcentral_settings() -> dict:
    es = yaml.safe_load((repo_root() / "meshcentral/meshcentral-config-es.yml").read_text())
    return json.loads(es["spec"]["target"]["template"]["data"]["config.json"])["settings"]


class O4i3m11MeshcentralTrustedProxy(unittest.TestCase):
    def test_trusted_proxy_is_the_pod_cidr_only(self):
        self.assertEqual(meshcentral_settings()["trustedProxy"], POD_CIDR)


if __name__ == "__main__":
    unittest.main()
