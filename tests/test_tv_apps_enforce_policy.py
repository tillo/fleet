"""The tv-apps policy admits exactly the callers the learn window proved, and denies the rest.

infra-o4i3m.4 (2026-11-10): Sonarr, Radarr, Prowlarr, Bazarr, Transmission, Tdarr and
Threadfin keep their auth only at the oauth2 ingress. Any pod could read the *arr API
keys (`/initialize.json`, Bazarr's HTML) and get code execution through Transmission's
`script-torrent-done` or a Tdarr flow. learn.tv-apps recorded every caller outside the
expected set from 2026-11-16 21:21Z for ~33 h (journald suppression 0 on every node):
the ONLY one was Home Assistant, on exactly its three integrations (radarr 7878,
sonarr 8989, tdarr 8265). Analysis: ~/tv-netpol-analysis-20261118.md.

Pinned here:
  * Inbound BitTorrent peers (51413 tcp+udp, internet sources) stay allowed, or
    Transmission loses every inbound peer.
  * ingress-nginx/Traefik and the tv namespace itself stay allowed.
  * Home Assistant reaches only its three integration ports.
  * Windmill WORKER pods reach only radarr 7878, sonarr 8989 and transmission 9091:
    f/heal/tv_seed_retention needs the *arr import history and the client to retire
    seed copies. Scoped to the workers, not the namespace — the app/frontend and
    postgres pods have no business reading API keys.
  * The end is Log then Deny: a block is answerable from netpol_flows. No unconditional
    Allow anywhere (the learn policy's trailing Allow must not survive).
  * Apps with their own in-app auth, and flaresolverr, are not selected.
"""

from __future__ import annotations

import re
import unittest

import yaml

from tests.fleetlib import repo_root

POLICY = repo_root() / "tv" / "tv-apps-netpol-enforce.yml"
LEARN = repo_root() / "tv" / "tv-apps-netpol-learn.yml"
APPS = {"sonarr", "radarr", "prowlarr", "bazarr", "tdarr", "threadfin", "transmission-pia"}
NOT_SELECTED = {"pms-docker", "seerr", "sftpgo", "autobrr", "flaresolverr",
                "tdarr-node-quarto", "plex-webhook-receiver"}
HA_PORTS = [7878, 8989, 8265]
WINDMILL_PORTS = [7878, 8989, 9091]


def spec() -> dict:
    return yaml.safe_load(POLICY.read_text())["spec"]


def rules() -> list[dict]:
    return spec()["ingress"]


def selected_apps() -> set[str]:
    m = re.fullmatch(r'app in \{(.*)\}', spec()["selector"])
    assert m, spec()["selector"]
    return {s.strip().strip('"') for s in m.group(1).split(",")}


def source_ns(rule: dict) -> str | None:
    sel = (rule.get("source") or {}).get("namespaceSelector") or ""
    m = re.fullmatch(r'kubernetes\.io/metadata\.name == "(.+)"', sel)
    return m.group(1) if m else None


class TvAppsEnforcePolicy(unittest.TestCase):
    def test_learn_policy_is_gone(self):
        self.assertFalse(LEARN.exists())

    def test_ingress_only_and_selects_exactly_the_seven_apps(self):
        self.assertEqual(spec()["types"], ["Ingress"])
        self.assertEqual(selected_apps(), APPS)
        self.assertFalse(selected_apps() & NOT_SELECTED)

    def test_ends_with_log_then_deny_and_never_allows_unconditionally(self):
        self.assertEqual(rules()[-2:], [{"action": "Log"}, {"action": "Deny"}])
        self.assertNotIn({"action": "Allow"}, rules())

    def test_bittorrent_peers_stay_allowed(self):
        for proto in ("TCP", "UDP"):
            with self.subTest(proto=proto):
                self.assertIn({"action": "Allow", "protocol": proto,
                               "destination": {"ports": [51413]}}, rules())

    def test_exactly_the_proven_namespaces_are_allowed(self):
        allowed = {source_ns(r) for r in rules() if r.get("action") == "Allow" and source_ns(r)}
        self.assertEqual(allowed, {"kube-system", "tv", "home-assistant", "windmill"})

    def test_windmill_workers_reach_only_the_arr_and_transmission_ports(self):
        wm = [r for r in rules() if source_ns(r) == "windmill"]
        self.assertEqual(wm, [{
            "action": "Allow", "protocol": "TCP",
            "source": {"namespaceSelector": 'kubernetes.io/metadata.name == "windmill"',
                       "selector": 'app.kubernetes.io/name == "windmill-workers"'},
            "destination": {"ports": WINDMILL_PORTS},
        }])

    def test_home_assistant_reaches_only_its_integrations(self):
        ha = [r for r in rules() if source_ns(r) == "home-assistant"]
        self.assertEqual(ha, [{
            "action": "Allow", "protocol": "TCP",
            "source": {"namespaceSelector": 'kubernetes.io/metadata.name == "home-assistant"',
                       "selector": 'app == "home-assistant"'},
            "destination": {"ports": HA_PORTS},
        }])

    def test_kube_system_means_the_ingress_controllers_only(self):
        ks = [r for r in rules() if source_ns(r) == "kube-system"]
        self.assertEqual(len(ks), 1)
        self.assertEqual(ks[0]["source"]["selector"],
                         'app.kubernetes.io/name in {"rke2-ingress-nginx", "rke2-traefik"}')


if __name__ == "__main__":
    unittest.main()
