"""The retired Tailscale range (100.64.0.0/10) is trusted nowhere.

The tailnet was emptied and Tailscale dropped on 2026-11-21. Two places still
treated its CGNAT range as "ours": ingress-nginx listed it in proxy-real-ip-cidr
(dormant while use-forwarded-headers is false, live the moment that flips), and the alertmanager relay classed a WAF block from it as a
likely false positive. A retired network's range left in an allow-list is trust
granted to whoever ends up holding it. (Netpols that list 100.64.0.0/10 under
`except:` to keep it OUT are correct and are not covered here.)
"""

import unittest

from tests.fleetlib import load_docs
from tests.test_am_relay_ingest_requires_the_fluentd_login import relay_module

HELM = "rke2-ingress-nginx-config/ingress-nginx-helmchartconfig.yml"


def real_ip_cidrs():
    for d in load_docs(HELM):
        if d and d.get("kind") == "HelmChartConfig":
            text = d["spec"]["valuesContent"]
            line = next(l for l in text.splitlines() if "proxy-real-ip-cidr:" in l)
            return [c.strip() for c in line.split(":", 1)[1].split(",")]
    raise AssertionError("no HelmChartConfig in " + HELM)


class RetiredTailnet(unittest.TestCase):
    def test_ingress_does_not_trust_forwarded_headers_from_100_64(self):
        cidrs = real_ip_cidrs()
        self.assertNotIn("100.64.0.0/10", cidrs)
        self.assertEqual(cidrs, ["10.0.0.0/8", "192.168.1.0/24"])

    def test_relay_no_longer_trusts_100_64_but_still_trusts_lan_and_cluster(self):
        trusted = relay_module().TRUSTED_IP_RE.match
        for ip in ("100.64.0.1", "100.100.1.2", "100.127.255.254"):
            self.assertIsNone(trusted(ip), ip)
        for ip in ("192.168.1.5", "10.52.0.5"):
            self.assertIsNotNone(trusted(ip), ip)


if __name__ == "__main__":
    unittest.main()
