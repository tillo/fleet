"""vmauth `vmsingle` is the authenticated front for every VictoriaMetrics client (infra-o4i3m.5.4).

vmsingle-mdapi takes /api/v1/import and /api/v1/write from any pod with no credential, so
a compromised pod can forge or overwrite any series, including the ones alerts key on.
The fix starts like the Pushgateway one (.5.2): a vmauth front with one user per writer,
each allowed only its own path, while the direct path stays open until every writer moved.

First writer: litellm's gheim-rt-guard (its code lives in the litellm DB row, not git). It
pushes Prometheus text to /api/v1/import/prometheus, and litellm's SSRF allowlist must
name the front's host or the push is refused.

Pinned here:
  * The VMAuth selects only users labelled for it, so no other VMUser can widen it.
  * The gheim-rt-guard user can reach exactly /api/v1/import/prometheus on vmsingle,
    with its password from an ExternalSecret (never a literal).
  * litellm's user_url_allowed_hosts names the front.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

FRONT = repo_root() / "monitoring-vm" / "vmauth-vmsingle.yml"
LITELLM_CM = repo_root() / "litellm" / "litellm-cm.yml"
LABEL = {"mdapi.ch/vmauth": "vmsingle"}
FRONT_HOST = "vmauth-vmsingle.monitoring.svc.cluster.local:8427"
VMSINGLE = "http://vmsingle-mdapi.monitoring.svc.cluster.local:8428"
# The PromQL read API and nothing else: no write, import, export, delete or admin path.
READ_PATHS = [
    "/api/v1/query", "/api/v1/query_range", "/api/v1/series", "/api/v1/labels",
    "/api/v1/label/.+/values", "/api/v1/metadata", "/api/v1/status/buildinfo",
    "/api/v1/buildinfo", "/api/v1/query_exemplars",
]
# Every client, and the only paths it may reach.
USERS = {
    "gheim-rt-guard": ["/api/v1/import/prometheus"],
    "vmagent-writer": ["/api/v1/write"],
    "vmalert-writer": ["/api/v1/write"],
    "grafana-reader": READ_PATHS,
    "vmalert-reader": READ_PATHS,
    "windmill-reader": READ_PATHS,
    "seat-reader": READ_PATHS,
    "ha-reader": READ_PATHS,
}
# Readers whose client is a Kubernetes object reading a Secret (the others hold the
# password in a Windmill variable or read Akeyless directly).
SECRET_CLIENTS = ("vmagent-writer", "vmalert-writer", "grafana-reader", "vmalert-reader")
# vmauth's own login TO vmsingle, carried on every route (vmsingle -httpAuth.*).
BACKEND_LOGIN = {"username": {"name": "vmsingle-httpauth", "key": "username"},
                 "password": {"name": "vmsingle-httpauth", "key": "password"}}


def docs() -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d
            for d in yaml.safe_load_all(FRONT.read_text()) if d}


def manifest(name: str) -> dict:
    return yaml.safe_load((repo_root() / "monitoring-vm" / name).read_text())


def login(secret: str) -> dict:
    return {"username": {"name": secret, "key": "username"},
            "password": {"name": secret, "key": "password"}}


class VmsingleWriteFront(unittest.TestCase):
    def test_vmauth_selects_only_its_own_users(self):
        spec = docs()[("VMAuth", "vmsingle")]["spec"]
        self.assertIs(spec["selectAllByDefault"], False)
        self.assertEqual(spec["userSelector"], {"matchLabels": LABEL})

    def test_each_user_reaches_only_its_own_paths(self):
        for name, paths in USERS.items():
            with self.subTest(user=name):
                user = docs()[("VMUser", name)]
                self.assertEqual(user["metadata"]["labels"], LABEL)
                self.assertEqual(user["spec"]["username"], name)
                self.assertEqual(user["spec"]["targetRefs"], [{
                    "static": {"url": VMSINGLE}, "paths": paths,
                    "targetRefBasicAuth": BACKEND_LOGIN}])

    def test_read_paths_can_never_write(self):
        for p in READ_PATHS:
            with self.subTest(path=p):
                self.assertNotRegex(p, r"write|import|export|delete|admin|snapshot|push")

    def test_backend_login_comes_from_akeyless(self):
        es = docs()[("ExternalSecret", "vmsingle-httpauth")]["spec"]
        self.assertEqual(es["target"]["name"], "vmsingle-httpauth")
        self.assertEqual(es["target"]["template"]["data"]["username"], "vmauth")
        self.assertEqual(es["data"][0]["remoteRef"]["key"],
                         "/mdapi/monitoring/vmsingle/httpauth-password")

    def test_no_user_beyond_the_known_clients(self):
        self.assertEqual({n for k, n in docs() if k == "VMUser"}, set(USERS))

    def test_each_password_comes_from_its_own_akeyless_item(self):
        d = docs()
        for name in USERS:
            with self.subTest(user=name):
                ref = d[("VMUser", name)]["spec"]["passwordRef"]
                es = d[("ExternalSecret", ref["name"])]["spec"]
                self.assertEqual(es["target"]["name"], ref["name"])
                self.assertEqual(es["data"][0]["remoteRef"]["key"],
                                 f"/mdapi/monitoring/vmauth/{name}-password")

    def test_remotewrite_clients_hold_their_own_login(self):
        # vmagent/vmalert read username AND password from the same Secret the
        # VMUser uses, so the two can never disagree after a rotation.
        d = docs()
        for name in SECRET_CLIENTS:
            with self.subTest(user=name):
                secret = d[("VMUser", name)]["spec"]["passwordRef"]["name"]
                tmpl = d[("ExternalSecret", secret)]["spec"]["target"]["template"]["data"]
                self.assertEqual(tmpl["username"], name)
                self.assertIn("password", tmpl)

    def test_litellm_allows_the_guard_to_reach_the_front(self):
        self.assertIn(f'"{FRONT_HOST}"', LITELLM_CM.read_text())

    def test_litellm_no_longer_lets_the_guard_write_vmsingle_directly(self):
        # Moved 2026-11-17 22:05Z; a leftover entry would let a reverted row write
        # unauthenticated again, and widens user-supplied URL fetches for nothing.
        self.assertNotIn('"vmsingle-mdapi.monitoring.svc.cluster.local:8428"',
                         LITELLM_CM.read_text())

    def test_vmagent_writes_vmsingle_only_through_the_front(self):
        rw = manifest("vmagent.yml")["spec"]["remoteWrite"][0]
        self.assertEqual(rw["url"], f"http://{FRONT_HOST}/api/v1/write")
        self.assertEqual(rw["basicAuth"], login("vmauth-vmagent-writer"))

    def test_vmalert_writes_only_through_the_front(self):
        rw = manifest("vmalert.yml")["spec"]["remoteWrite"]
        # vmalert appends /api/v1/write itself.
        self.assertEqual(rw["url"], f"http://{FRONT_HOST}")
        self.assertEqual(rw["basicAuth"], login("vmauth-vmalert-writer"))

    def test_no_remotewrite_targets_vmsingle_directly(self):
        for name in ("vmagent.yml", "vmalert.yml"):
            with self.subTest(file=name):
                rw = manifest(name)["spec"]["remoteWrite"]
                urls = [r["url"] for r in (rw if isinstance(rw, list) else [rw])]
                self.assertFalse([u for u in urls if "vmsingle-mdapi" in u])

    def test_vmalert_reads_through_the_front_with_its_reader_login(self):
        spec = manifest("vmalert.yml")["spec"]
        for key in ("datasource", "remoteRead"):
            with self.subTest(block=key):
                self.assertEqual(spec[key]["url"], f"http://{FRONT_HOST}")
                self.assertEqual(spec[key]["basicAuth"], login("vmauth-vmalert-reader"))

    def test_grafana_datasources_read_through_the_front(self):
        cm = yaml.safe_load((repo_root() / "monitoring-grafana" / "grafana-datasources-cm.yml")
                            .read_text())
        ds = yaml.safe_load(cm["data"]["datasources.yaml"])["datasources"]
        vm = [d for d in ds if d["type"] == "prometheus"]
        self.assertEqual(len(vm), 2)  # Main Org + the anonymous Embed org twin
        for d in vm:
            with self.subTest(org=d.get("orgId", 1)):
                self.assertEqual(d["url"], f"http://{FRONT_HOST}")
                self.assertIs(d["basicAuth"], True)
                self.assertEqual(d["basicAuthUser"], "$VM_READER_USER")
                self.assertEqual(d["secureJsonData"], {"basicAuthPassword": "$VM_READER_PASSWORD"})

    def test_grafana_gets_the_reader_login_from_its_secret(self):
        dep = yaml.safe_load((repo_root() / "monitoring-grafana" / "grafana-deploy.yml").read_text())
        env = {e["name"]: e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
        for var, key in (("VM_READER_USER", "username"), ("VM_READER_PASSWORD", "password")):
            with self.subTest(var=var):
                self.assertEqual(env[var]["valueFrom"]["secretKeyRef"],
                                 {"name": "vmauth-grafana-reader", "key": key})

    def test_nothing_in_fleet_reads_vmsingle_directly(self):
        # Only vmauth itself (its routes) and vmsingle's own manifests may name it.
        allowed = {"monitoring-vm/vmauth-vmsingle.yml", "monitoring-vm/vmsingle.yml",
                   "monitoring-vm/vmsingle-admin-authkey-es.yml"}
        hits = []
        for p in repo_root().rglob("*.y*ml"):
            rel = p.relative_to(repo_root()).as_posix()
            if rel.startswith(("tests/", ".")) or rel in allowed:
                continue
            for line in p.read_text(errors="replace").splitlines():
                if "vmsingle-mdapi.monitoring.svc" in line and not line.lstrip().startswith("#"):
                    hits.append(f"{rel}: {line.strip()}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
