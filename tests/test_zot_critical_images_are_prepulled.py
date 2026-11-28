"""The CRITICAL zot-served images are cached on every node (infra-8f4.68, option A).

WHY (2026-11-21): zot.mdapi.ch stores in a Ceph RGW bucket. Ceph went HEALTH_ERR, RGW
crashlooped, zot answered 503, and every pod needing a zot image to start sat in
ImagePullBackOff -- 141 containers in 40 namespaces that day. The owner chose to keep zot on
Ceph and pre-pull what logins, DNS, alerting and secrets need (decision card
~/decision-cards/zot-registry-ceph-dependency-2026-11-21.md):
  Keycloak (ns keycloak), oauth2-proxy + its redis session stores, Unbound (split
  horizon), the public BIND nameserver, OpenBao, and vmsingle/vmagent/vmalert/
  vmalertmanager. Measured from the live pods: the envuassu Keycloak, Technitium and
  vmauth are NOT zot-served, and Keycloak's database is CNPG keycloak-pg (ghcr.io), so
  the zot postgres:17 Deployment in ns keycloak is not on the login path.

TWO DaemonSets, both isolated from the seat and recovery pre-pullers (a zot outage
then only stalls THEIR rollouts), because the consumers pin two different ways:
  * zot-critical-image-prepull -- Keycloak, Unbound, nameserver, OpenBao are pinned in
    git as one `image:` line and re-pinned almost daily (45/37/36/6 bumps in 30 days).
    The pre-puller carries the IDENTICAL line, Renovate stays ON for it and rewrites
    every occurrence of a package in one MR (as for the seat); this test fails if any
    consumer line is missing from it.
  * zot-critical-live-prepull -- oauth2-proxy + redis (tag@digest split across Helm
    values fields) and the VictoriaMetrics CRs (tag from the operator's defaults) have
    no single line to mirror and move rarely (0-3 bumps in 30 days). That file is
    DERIVED from the live pods by tools/recovery-prepull-sync.py and Renovate is OFF
    for it, as for the recovery images.
Alert ZotCriticalImageNotPrepulled (monitoring-rules/88) compares, digest by digest,
every running critical zot container with what the two DaemonSets hold.
"""

from __future__ import annotations

import json
import unittest

import fleetlib
from test_recovery_images_are_prepulled_on_every_node import D1, D2, pod, sync
from test_seat_image_is_prepulled_on_every_node import containers, find, is_pinned, pod_spec, tolerates_everything

LOCKSTEP = ("DaemonSet", "zot-critical-image-prepull")
LIVE = ("DaemonSet", "zot-critical-live-prepull")
LIVE_FILE = "jump/seat-prepull/zot-critical-live-prepull.yml"
LOCKSTEP_FILE = "jump/seat-prepull/zot-critical-image-prepull.yml"
ISOLATED_FROM = (("DaemonSet", "seat-image-prepull"), ("DaemonSet", "recovery-image-prepull"))
ZOT = "zot.mdapi.ch/"

# (file, kind, name): the critical consumers pinned as one `image:` line in git.
CONSUMERS = (
    ("keycloak/keycloak-deploy.yml", "Deployment", "keycloak"),
    ("split-horizon/unbound-deploy.yml", "Deployment", "unbound"),
    ("nameserver/nameserver-deploy.yml", "Deployment", "nameserver"),
    ("openbao-bootstrap/openbao-bootstrap-cronjob.yml", "CronJob", "openbao-bootstrap"),
)


def workload_containers(doc: dict) -> list[dict]:
    """Containers of a Deployment/StatefulSet/DaemonSet or a CronJob's job template."""
    spec = doc.get("spec") or {}
    if doc.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    tpl = (spec.get("template") or {}).get("spec") or {}
    return [c for c in (tpl.get("containers") or []) + (tpl.get("initContainers") or []) if isinstance(c, dict)]


def consumer_images() -> set[str]:
    out = set()
    for rel, kind, name in CONSUMERS:
        docs = [d for d in fleetlib.load_docs(rel)
                if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name]
        assert len(docs) == 1, f"{kind}/{name} not found in {rel} -- update CONSUMERS"
        out |= {c["image"] for c in workload_containers(docs[0]) if str(c.get("image", "")).startswith(ZOT)}
    return out


class LiveScopeTest(unittest.TestCase):
    """The zot-critical-live set: which live pods and which of their images count."""

    scopes = sync.SETS["zot-critical-live"].scopes

    def test_only_zot_images_of_the_critical_pods(self):
        pods = [pod("monitoring", "vmalert-mdapi-7d-x1", [
                    ("vmalert", "zot.mdapi.ch/victoriametrics/vmalert:v1", "zot.mdapi.ch/victoriametrics/vmalert@" + D1),
                    ("config-reloader", "victoriametrics/operator:config-reloader-v1", "docker.io/victoriametrics/operator@" + D2)]),
                pod("monitoring", "grafana-5-x", [("g", "zot.mdapi.ch/grafana/grafana:1", "zot.mdapi.ch/grafana/grafana@" + D2)]),
                pod("monitoring", "vm-operator-victoria-metrics-operator-1-x", [
                    ("o", "zot.mdapi.ch/victoriametrics/operator:v1", "zot.mdapi.ch/victoriametrics/operator@" + D2)]),
                pod("oauth2-proxy", "oauth2-proxy-tv-redis-ha-server-0", [
                    ("redis", "zot.mdapi.ch/library/redis:8@" + D2, "zot.mdapi.ch/library/redis@" + D2)])]
        self.assertEqual(sync.derive(pods, self.scopes),
                         ["zot.mdapi.ch/library/redis:8@" + D2, "zot.mdapi.ch/victoriametrics/vmalert:v1@" + D1])

    def test_lockstep_consumers_are_not_live_derived(self):
        sso_pod = "-".join(("keycloak", "0123456789", "aaaaa"))   # a Deployment pod name
        pods = [pod("keycloak", sso_pod, [("k", "zot.mdapi.ch/mdapi/keycloak:latest", "x@" + D1)]),
                pod("split-horizon", "unbound-6f-x", [("u", "zot.mdapi.ch/mdapi/unbound:1", "x@" + D1)])]
        self.assertEqual(sync.derive(pods, self.scopes), [])


class ZotCriticalManifestTest(unittest.TestCase):
    def daemonset(self, ref):
        found = find(ref)
        self.assertEqual(len(found), 1, f"exactly one {ref} expected")
        return found[0]

    def test_every_consumer_line_is_held_identically(self):
        wanted = consumer_images()
        self.assertGreaterEqual(len(wanted), len(CONSUMERS), "discovery found too few consumer images")
        held = {c["image"] for c in containers(self.daemonset(LOCKSTEP)[1], init=False)}
        self.assertEqual(wanted - held, set(), "bump the consumer and the pre-puller in one MR")
        self.assertEqual(held - wanted, set(), "the lockstep pre-puller holds an image no consumer pins")

    def test_both_daemonsets_are_isolated_pinned_and_on_every_node(self):
        others = {c["image"] for ref in ISOLATED_FROM for c in containers(self.daemonset(ref)[1], init=False)}
        for ref, rel in ((LOCKSTEP, LOCKSTEP_FILE), (LIVE, LIVE_FILE)):
            path, ds = self.daemonset(ref)
            with self.subTest(daemonset=ref[1]):
                self.assertEqual(path, rel)
                spec = pod_spec(ds)
                holders = containers(ds, init=False)
                self.assertGreaterEqual(len(holders), 4)
                self.assertEqual({c["image"] for c in holders} & others, set())
                for c in containers(ds):
                    self.assertTrue(is_pinned(c["image"]), c["image"])
                    self.assertEqual(c.get("imagePullPolicy"), "IfNotPresent")
                for c in holders:
                    self.assertEqual(c.get("command", [None])[0], sync.HOLD_BIN)
                stage = spec.get("initContainers") or []
                self.assertEqual(len(stage), 1)
                self.assertIn("-musl@", stage[0]["image"])
                self.assertIn({"name": "zot-pull"}, spec.get("imagePullSecrets") or [])
                self.assertTrue(tolerates_everything(spec))
                rolling = (ds["spec"].get("updateStrategy") or {}).get("rollingUpdate") or {}
                self.assertEqual((rolling.get("maxUnavailable"), rolling.get("maxSurge")), (0, 1))

    def test_live_block_is_what_the_tool_writes(self):
        holders = [c["image"] for c in containers(self.daemonset(LIVE)[1], init=False)]
        text = (fleetlib.repo_root() / LIVE_FILE).read_text(encoding="utf-8")
        self.assertEqual(sync.rewrite(text, holders), text, "hand-edited block: run the tool")
        self.assertEqual(sync.SETS["zot-critical-live"].path, fleetlib.repo_root() / LIVE_FILE)

    def test_renovate_off_for_the_live_file_only(self):
        cfg = json.loads((fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8"))
        off = [g for r in cfg["packageRules"] if r.get("enabled") is False for g in r.get("matchFileNames") or []]
        self.assertIn(LIVE_FILE, off)
        self.assertNotIn(LOCKSTEP_FILE, off, "the lockstep file must stay Renovate-tracked")


if __name__ == "__main__":
    unittest.main()
