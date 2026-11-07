"""Every host in the WAF's DetectionOnly list (rule 1913) is there for a recorded reason.

Incident (infra-lcrt, measured 2026-11-01): rule 1913 was written on 2026-09-15 as a SOAK
list -- "promote by deleting from this list, one host at a time, after checking its audit
log is clean" -- and nobody deleted anything for seven weeks. By then it held 26 entries:
21 hosts whose audit log WAS clean (every request scoring >= 10 over 7 days was a scanner),
one host with no Ingress at all (santillo.tillo.ch), and one literal `*.pages.mdapi.ch`
that no real Host header can ever equal. A soak list with no exit check is a permanent
exemption that nobody decided on.

DetectionOnly logs an attack and lets it through. On a public host that is no WAF at all,
so the list must stay short and every entry must say why it cannot be enforced. To add a
host, add it to SANCTIONED below with that reason; this test is where the decision lives.
"""

import re
import unittest

import fleetlib

# host -> why it cannot be enforced. Keep the reason specific enough to re-check.
SANCTIONED = {
    "notes.mdapi.ch": (
        "Joplin sync: raw note bodies and sync blobs are CRS-hostile by nature; the app is "
        "token-authenticated (sanctioned public exception, fleet/joplin/joplin-ing.yml)."
    ),
    "grafana-embed.mdapi.ch": (
        "d-solo panel embeds whose VictoriaMetrics proxy queries trip 942100/942151; "
        "allowlisted to the LAN (monitoring-grafana/grafana-embed-ing.yml)."
    ),
    "pushgateway.mdapi.ch": (
        "bpi-r4 and mbp-mda push Prometheus text as curl's default form-urlencoded body, "
        "so every metric line becomes an ARGS_NAME: 921150 on every push, up to score 15 "
        "with 930120/932230/932235/932250/942100. Enforcing it drops the router's metrics. "
        "LAN-allowlisted. Promote once the pushers send Content-Type: text/plain."
    ),
}

DETECTION_ONLY_LIST = re.compile(
    r'SecRule\s+REQUEST_HEADERS:Host\s+"@rx\s+\^\(([^)]*)\)\(\?::\[0-9\]\+\)\?\$"\s*\\\s*\n'
    r'\s*"id:1913,[^"]*ctl:ruleEngine=DetectionOnly"')


def detection_only_hosts(conf: str) -> set[str]:
    m = DETECTION_ONLY_LIST.search(conf)
    if not m:
        raise AssertionError(
            "measured NOTHING: rule id:1913 (the named DetectionOnly list) did not parse. "
            "If the list was emptied, delete the rule and this test with it; if its shape "
            "changed, update DETECTION_ONLY_LIST."
        )
    return {alt.replace("\\", "") for alt in m.group(1).split("|")}


class DetectionOnlyHostsTest(unittest.TestCase):
    def test_every_detection_only_host_is_sanctioned(self):
        hosts = detection_only_hosts(fleetlib.modsec_global_conf())
        self.assertTrue(hosts, "measured NOTHING: rule 1913 names no host")

        unsanctioned = sorted(hosts - SANCTIONED.keys())
        self.assertEqual(
            unsanctioned, [],
            "hosts left in DetectionOnly (rule 1913) with no recorded reason. Enforce them "
            "(delete them from the list) or add them to SANCTIONED with the reason:\n  "
            + "\n  ".join(unsanctioned)
        )

    def test_no_stale_sanction(self):
        hosts = detection_only_hosts(fleetlib.modsec_global_conf())
        stale = sorted(SANCTIONED.keys() - hosts)
        self.assertEqual(
            stale, [],
            "SANCTIONED names hosts that rule 1913 no longer lists; drop them here so the "
            "exemption cannot quietly come back:\n  " + "\n  ".join(stale)
        )


if __name__ == "__main__":
    unittest.main()
