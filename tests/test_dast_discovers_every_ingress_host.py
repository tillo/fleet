"""The edge DAST scan must onboard every new Ingress host on its own.

infra-7rog.31.6 (2026-11-10): the user wants every exposed application scanned, and every NEW
one onboarded with no edit. The hand list `dast/targets.yml` held 1 host out of 96 live Ingress
hosts, so it is replaced by `discover.py`, run as the edge-dast init container: it lists the
cluster's Ingresses through the API and writes the host list the scanners read.

What breaks the promise if it regresses:
  * a host left out silently: every Ingress host counts, whatever its class or namespace, and
    an annotated opt-out (`dast.mdapi.ch/skip: <reason>`) is the ONLY way out -- the honeypot
    uses it, because scanning a trap fills it with our own traffic;
  * a wildcard (`*.mdapi.ch`) is not a host a scanner can visit, so it is dropped, not passed;
  * the daily share must be a stable PARTITION: every host lands in exactly one of SHARDS
    days, so each host is scanned every SHARDS days and none is never scanned;
  * an empty discovery must FAIL the pod: a scan of nothing would import as a clean night.

infra-7rog.31.7 (same day): the non-HTTP exposures (mail, DNS, NTS, SFTP, rsync, FTP, TURN,
IRC, Plex, MQTT, ...) are every TCP port of every LoadBalancer Service, discovered the same
way and written to /targets/endpoints for the `nuclei-net` container -- one line per Service,
`<name>.<ns>.svc <ip> <port,port,...>`, all of them every night (about 30 Services). The
same skip annotation opts a Service out (the honeypot; the ingress, which edge DAST covers).
"""

from __future__ import annotations

import datetime
import json
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import yaml

from tests.fleetlib import repo_root

CONFIGMAP = Path(repo_root()) / "dast" / "discover.yml"


def _guard(*_a, **_k):
    raise AssertionError("discover.py reached the network in a test")


def load() -> types.ModuleType:
    doc = yaml.safe_load(CONFIGMAP.read_text())
    mod = types.ModuleType("discover")
    exec(compile(doc["data"]["discover.py"], "discover.py", "exec"), mod.__dict__)
    # The module's OWN urllib is a stub whose urlopen raises: a mutant that skips a seam
    # hits this, never the real API (2026-11-10, a mutant overwrote a live secret). Scoped
    # to this module, so tests elsewhere in the suite keep the real network.
    mod.urllib = types.SimpleNamespace(request=types.SimpleNamespace(
        Request=urllib.request.Request, urlopen=_guard))
    return mod


D = load()


def ing(*hosts, skip=None, ns="app"):
    meta = {"name": "x", "namespace": ns, "annotations": {}}
    if skip is not None:
        meta["annotations"]["dast.mdapi.ch/skip"] = skip
    return {"metadata": meta, "spec": {"rules": [{"host": h} for h in hosts] + [{"http": {}}]}}


class IngressHosts(unittest.TestCase):
    def test_every_host_of_every_ingress_once_sorted(self):
        got = D.ingress_hosts([ing("b.example", "a.example"), ing("a.example", ns="other")])
        self.assertEqual(got, ["a.example", "b.example"])

    def test_wildcards_are_dropped(self):
        self.assertEqual(D.ingress_hosts([ing("*.example", "c.example")]), ["c.example"])

    def test_skip_annotation_removes_the_ingress(self):
        self.assertEqual(D.ingress_hosts([ing("trap.example", skip="honeypot"), ing("d.example")]),
                         ["d.example"])

    def test_skip_without_a_reason_is_refused(self):
        with self.assertRaises(ValueError):
            D.ingress_hosts([ing("e.example", skip="")])

    def test_a_host_skipped_on_one_ingress_but_served_by_another_stays(self):
        self.assertEqual(D.ingress_hosts([ing("f.example", skip="x"), ing("f.example")]),
                         ["f.example"])

    def test_missing_annotations_and_rules_are_tolerated(self):
        self.assertEqual(D.ingress_hosts([{"metadata": {}, "spec": {}}]), [])


def svc(name, ns="app", ip="192.0.2.7", ports=((443, "TCP"),), kind="LoadBalancer", skip=None):
    meta = {"name": name, "namespace": ns, "annotations": {}}
    if skip is not None:
        meta["annotations"]["dast.mdapi.ch/skip"] = skip
    status = {"loadBalancer": {"ingress": [{"ip": ip}] if ip else []}}
    return {"metadata": meta, "spec": {"type": kind, "ports": [{"port": p, "protocol": pr} for p, pr in ports]},
            "status": status}


class LoadBalancerEndpoints(unittest.TestCase):
    def test_one_line_per_service_with_its_tcp_ports(self):
        got = D.lb_endpoints([svc("mail", ns="m", ip="192.0.2.48", ports=((993, "TCP"), (25, "TCP"), (53, "UDP")))])
        self.assertEqual(got, ["mail.m.svc 192.0.2.48 25,993"])

    def test_tcp_and_udp_on_the_same_port_count_once(self):
        got = D.lb_endpoints([svc("dns", ports=((53, "TCP"), (53, "UDP")))])
        self.assertEqual(got, ["dns.app.svc 192.0.2.7 53"])

    def test_only_loadbalancers_with_an_address_and_a_tcp_port(self):
        items = [svc("a", kind="ClusterIP"), svc("b", ip=None), svc("c", ports=((123, "UDP"),)), svc("d")]
        self.assertEqual(D.lb_endpoints(items), ["d.app.svc 192.0.2.7 443"])

    def test_sorted_and_skippable(self):
        items = [svc("z"), svc("trap", skip="honeypot"), svc("a", ns="b")]
        self.assertEqual(D.lb_endpoints(items), ["a.b.svc 192.0.2.7 443", "z.app.svc 192.0.2.7 443"])

    def test_skip_without_a_reason_is_refused(self):
        with self.assertRaises(ValueError):
            D.lb_endpoints([svc("x", skip=" ")])

    def test_missing_status_and_ports_are_tolerated(self):
        self.assertEqual(D.lb_endpoints([{"metadata": {"name": "n", "namespace": "s"},
                                          "spec": {"type": "LoadBalancer"}}]), [])


class StaticHosts(unittest.TestCase):
    def test_comments_and_blanks_are_ignored(self):
        self.assertEqual(D.static_hosts("# c\n\n cm.example \nx.example\n"), ["cm.example", "x.example"])


class Share(unittest.TestCase):
    HOSTS = [f"h{i}.example" for i in range(60)]

    def test_shards_partition_the_hosts(self):
        days = [D.todays_share(self.HOSTS, day) for day in range(D.SHARDS)]
        self.assertEqual(sorted(sum(days, [])), sorted(self.HOSTS))
        self.assertTrue(all(days), "a shard is empty on 60 hosts: the hash is not spreading")

    def test_share_repeats_every_shards_days(self):
        self.assertEqual(D.todays_share(self.HOSTS, 7), D.todays_share(self.HOSTS, 7 + D.SHARDS))

    def test_share_is_stable_across_runs(self):
        # sha256, not hash(): Python salts hash() per process, which would reshuffle daily.
        self.assertEqual(D.shard_of("backstage.mdapi.ch"), D.shard_of("backstage.mdapi.ch"))
        self.assertIn(D.shard_of("backstage.mdapi.ch"), range(D.SHARDS))

    def test_epoch_day(self):
        self.assertEqual(D.epoch_day(datetime.date(1970, 1, 3)), 2)

    def test_today_honours_a_manual_pin(self):
        with mock.patch.dict(D.os.environ, {"DAST_DAY": "6"}):
            self.assertEqual(D.today(), 6)
        with mock.patch.dict(D.os.environ, {"DAST_DAY": ""}):
            self.assertEqual(D.today(), D.epoch_day(datetime.datetime.now(datetime.timezone.utc).date()))


class Run(unittest.TestCase):
    def run_with(self, ingresses, services=(svc("lb"),), static="", day=0):
        written = {}
        lists = {D.INGRESSES: list(ingresses), D.SERVICES: list(services)}
        with mock.patch.object(D, "list_items", side_effect=lambda path: lists[path]), \
             mock.patch.object(D, "read_static", return_value=static), \
             mock.patch.object(D, "write_lines", side_effect=lambda path, ls: written.setdefault(path, ls)), \
             mock.patch.object(D, "today", return_value=day):
            rc = D.run()
        return rc, written

    def test_writes_todays_host_share_and_every_endpoint(self):
        hosts = [f"h{i}.example" for i in range(30)]
        rc, written = self.run_with([ing(*hosts)], static="cm.example\n", day=1)
        self.assertEqual(rc, 0)
        self.assertEqual(written[D.OUT], D.todays_share(sorted(hosts + ["cm.example"]), 1))
        self.assertEqual(written[D.ENDPOINTS], ["lb.app.svc 192.0.2.7 443"])

    def test_no_hosts_fails_and_writes_nothing(self):
        rc, written = self.run_with([], static="")
        self.assertEqual((rc, written), (1, {}))

    def test_no_endpoints_fails_and_writes_nothing(self):
        rc, written = self.run_with([ing("a.example")], services=[])
        self.assertEqual((rc, written), (1, {}))


class WriteLines(unittest.TestCase):
    def test_one_line_each_newline_terminated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/out"
            D.write_lines(p, ["a", "b"])
            self.assertEqual(Path(p).read_text(), "a\nb\n")


class Api(unittest.TestCase):
    def test_list_ingresses_reads_items_with_the_token(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"items": [ing("g.example")]}).encode()

        def fake(req, context=None, timeout=None):
            seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
            return Resp()

        with mock.patch.object(D, "token", return_value="T"), \
             mock.patch.object(D, "tls_context", return_value=None), \
             mock.patch.object(D.urllib.request, "urlopen", side_effect=fake):
            items = D.list_items(D.INGRESSES)
        self.assertEqual(D.ingress_hosts(items), ["g.example"])
        self.assertEqual(seen["url"], "https://kubernetes.default.svc/apis/networking.k8s.io/v1/ingresses")
        self.assertEqual(seen["auth"], "Bearer T")
        self.assertGreater(seen["timeout"], 0)


class Manifest(unittest.TestCase):
    """The pod wiring the promise depends on."""

    def setUp(self):
        self.cron = yaml.safe_load((Path(repo_root()) / "dast" / "cronjob.yml").read_text())
        self.pod = self.cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]

    def test_discovery_runs_first_and_only_it_holds_a_token(self):
        self.assertFalse(self.pod["automountServiceAccountToken"])
        (init,) = self.pod["initContainers"]
        token_vols = {v["name"] for v in self.pod["volumes"] if "projected" in v}
        self.assertTrue(token_vols & {m["name"] for m in init["volumeMounts"]})
        for c in self.pod["containers"]:
            self.assertFalse(token_vols & {m["name"] for m in c["volumeMounts"]}, c["name"])

    def test_scanners_read_the_discovered_lists(self):
        reads = {"zap": "/targets/hosts", "nuclei": "/targets/hosts", "nuclei-net": "/targets/endpoints"}
        self.assertEqual({c["name"] for c in self.pod["containers"]}, set(reads))
        for c in self.pod["containers"]:
            self.assertIn(reads[c["name"]], c["args"][0], c["name"])
            self.assertIn("targets", {m["name"] for m in c["volumeMounts"]})

    def test_network_scan_never_runs_attack_templates(self):
        (net,) = [c for c in self.pod["containers"] if c["name"] == "nuclei-net"]
        excluded = net["args"][0].split("-etags ", 1)[1].split()[0].split(",")
        for tag in ("brute", "bruteforce", "default-login", "intrusive", "dos", "fuzz"):
            self.assertIn(tag, excluded)
        self.assertIn("-ni", net["args"][0].split())

    def test_nuclei_containers_do_not_share_a_template_home(self):
        homes = [m["name"] for c in self.pod["containers"] if c["name"].startswith("nuclei")
                 for m in c["volumeMounts"] if m["mountPath"].startswith("/home/")]
        self.assertEqual(len(homes), len(set(homes)), homes)

    def test_rbac_lists_ingresses_and_services_only(self):
        docs = list(yaml.safe_load_all((Path(repo_root()) / "dast" / "rbac" / "rbac.yml").read_text()))
        (role,) = [d for d in docs if d and d["kind"] == "ClusterRole"]
        self.assertEqual(sorted((r["apiGroups"][0], res, tuple(r["verbs"])) for r in role["rules"]
                                for res in r["resources"]),
                         [("", "services", ("list",)), ("networking.k8s.io", "ingresses", ("list",))])

    def test_honeypot_opts_out(self):
        doc = yaml.safe_load((Path(repo_root()) / "honeypot" / "trapeye-ing.yml").read_text())
        self.assertTrue(doc["metadata"]["annotations"].get("dast.mdapi.ch/skip"))

    def test_honeypot_and_ingress_loadbalancers_opt_out(self):
        for rel, name in (("honeypot/trapeye-svc.yml", "trapeye-loadbalancer"),
                          ("ingress/ingress-expose-svc.yml", "ingress-expose")):
            (doc,) = [d for d in yaml.safe_load_all((Path(repo_root()) / rel).read_text())
                      if d and d["metadata"]["name"] == name]
            self.assertTrue((doc["metadata"].get("annotations") or {}).get("dast.mdapi.ch/skip"), rel)

    def test_run_fits_before_the_morning_import(self):
        # 04:41Z start; the DefectDojo sync reads the newest FINISHED pod at 06:30Z.
        self.assertLessEqual(self.cron["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"], 6000)


if __name__ == "__main__":
    unittest.main()
