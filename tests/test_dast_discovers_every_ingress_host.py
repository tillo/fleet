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

infra-7rog.31.9 (2026-11-23): every exposure PATH, not only Ingress + LoadBalancer. The
router's DNAT redirects, haproxy binds and Jool NAT64 redirects reach the scan through
ConfigMap dast/dast-exposure (windmill f/security/dast_exposure_inventory, hourly), so a new
router app is picked up with no commit and no hostname is ever written here. Targets are
deduped by BACKEND (public front first), unreachable addresses never reach the scanner, and
the log reports `coverage: N paths enumerated, M reachable`. A stale or half-read inventory
fails the run; targets.yml (the hand-listed haproxy host) is gone -- its host now comes from
the haproxy certificate.
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
    """With no router path, every LoadBalancer TCP port is scanned at its own address."""

    def lines(self, items):
        return D.endpoint_lines(D.plan(items, []))

    def test_one_line_per_service_with_its_tcp_ports(self):
        got = self.lines([svc("mail", ns="m", ip="192.0.2.48", ports=((993, "TCP"), (25, "TCP"), (53, "UDP")))])
        self.assertEqual(got, ["mail.m.svc 192.0.2.48:25,192.0.2.48:993"])

    def test_tcp_and_udp_on_the_same_port_scan_once(self):
        self.assertEqual(self.lines([svc("dns", ports=((53, "TCP"), (53, "UDP")))]), ["dns.app.svc 192.0.2.7:53"])

    def test_only_loadbalancers_with_an_address_and_a_tcp_port(self):
        items = [svc("a", kind="ClusterIP"), svc("b", ip=None), svc("c", ip="192.0.2.8", ports=((123, "UDP"),)),
                 svc("d")]
        self.assertEqual(self.lines(items), ["d.app.svc 192.0.2.7:443"])

    def test_sorted_and_skippable(self):
        items = [svc("z"), svc("trap", ip="192.0.2.45", skip="honeypot"), svc("a", ns="b", ip="192.0.2.9")]
        self.assertEqual(self.lines(items), ["a.b.svc 192.0.2.9:443", "z.app.svc 192.0.2.7:443"])

    def test_skip_without_a_reason_is_refused(self):
        with self.assertRaises(ValueError):
            self.lines([svc("x", skip=" ")])

    def test_missing_status_and_ports_are_tolerated(self):
        self.assertEqual(self.lines([{"metadata": {"name": "n", "namespace": "s"},
                                      "spec": {"type": "LoadBalancer"}}]), [])


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


# ---------------------------------------------------------------------------------------
# infra-7rog.31.9: the router's exposure paths (DNAT, haproxy, Jool NAT64), carried in by
# windmill f/security/dast_exposure_inventory as ConfigMap dast/dast-exposure (`raw` is the
# bpi-r4 gate's `exposure` output, `generated` its read time). Documentation addresses only.
RAW = """## dnat
firewall.@redirect[0]=redirect
firewall.@redirect[0].target='DNAT'
firewall.@redirect[0].src='wan'
firewall.@redirect[0].src_dip='198.51.100.5'
firewall.@redirect[0].src_dport='873'
firewall.@redirect[0].dest_ip='192.0.2.49'
firewall.@redirect[0].proto='tcp'
firewall.@redirect[1]=redirect
firewall.@redirect[1].src='lan'
firewall.@redirect[1].src_dip='198.51.100.5'
firewall.@redirect[1].src_dport='873'
firewall.@redirect[1].dest_ip='192.0.2.49'
firewall.@redirect[2]=redirect
firewall.@redirect[2].enabled='0'
firewall.@redirect[2].src='wan'
firewall.@redirect[2].src_dip='198.51.100.5'
firewall.@redirect[2].src_dport='9999'
firewall.@redirect[2].dest_ip='192.0.2.99'
firewall.@redirect[3]=redirect
firewall.@redirect[3].target='SNAT'
firewall.@redirect[3].src='wan'
firewall.@redirect[3].src_dport='8888'
firewall.@redirect[3].dest_ip='192.0.2.98'
firewall.@redirect[4]=redirect
firewall.@redirect[4].src='wan'
firewall.@redirect[4].src_dip='198.51.100.6'
firewall.@redirect[4].src_dport='40000-40002'
firewall.@redirect[4].dest_ip='192.0.2.50'
firewall.@redirect[4].proto='tcp' 'udp'
firewall.@redirect[5]=redirect
firewall.@redirect[5].src='wan'
firewall.@redirect[5].src_dip='2001:db8::5'
firewall.@redirect[5].src_dport='873'
firewall.@redirect[5].dest_ip='2001:db8:64::192.0.2.49'
firewall.@redirect[5].proto='tcp'
firewall.@redirect[6]=redirect
firewall.@redirect[6].src='wan'
firewall.@redirect[6].src_dip='2001:db8::6'
firewall.@redirect[6].src_dport='2443'
firewall.@redirect[6].dest_port='443'
firewall.@redirect[6].dest_ip='2001:db8:64::c000:23e'
firewall.@redirect[6].proto='tcp'
## haproxy
frontend cm
\tbind 198.51.100.7:443 name x ssl alpn h2,http/1.1 crt /etc/ssl/acme/cm.example.combined.crt
\tbind [2001:db8::7]:443 name y ssl crt /etc/ssl/acme/cm.example.combined.crt
\tdefault_backend cm_pool
backend cm_pool
\tserver cm 10.9.9.9:443 ssl verify none
frontend mail-smtp
\tbind 198.51.100.8:25 name 198.51.100.8:25
\tdefault_backend mail-smtp_pool
backend mail-smtp_pool
\tserver mail-smtp 192.0.2.48:12525 send-proxy
## jool
--pool6 2001:db8:64::/96
"""


def exposure(raw=RAW, generated="2026-11-23T03:17:00Z"):
    return {"raw": raw, "generated": generated}


NOW = datetime.datetime(2026, 11, 23, 4, 41, tzinfo=datetime.timezone.utc)


class RouterPaths(unittest.TestCase):
    def paths(self, raw=RAW):
        return D.router_paths(raw)

    def test_dnat_wan_enabled_dnat_only_one_path_per_port_and_protocol(self):
        dnat = sorted((p.proto, p.front, p.back) for p in self.paths() if p.kind == "dnat")
        self.assertEqual(dnat, [
            ("tcp", "198.51.100.5:873", "192.0.2.49:873"),
            ("tcp", "198.51.100.6:40000", "192.0.2.50:40000"),
            ("tcp", "198.51.100.6:40001", "192.0.2.50:40001"),
            ("tcp", "198.51.100.6:40002", "192.0.2.50:40002"),
            ("udp", "198.51.100.6:40000", "192.0.2.50:40000"),
            ("udp", "198.51.100.6:40001", "192.0.2.50:40001"),
            ("udp", "198.51.100.6:40002", "192.0.2.50:40002"),
        ])

    def test_a_v6_redirect_into_pool6_is_a_nat64_path_to_the_embedded_v4_host(self):
        nat64 = sorted((p.front, p.back) for p in self.paths() if p.kind == "nat64")
        self.assertEqual(nat64, [("[2001:db8::5]:873", "192.0.2.49:873"),
                                 ("[2001:db8::6]:2443", "192.0.2.62:443")])

    def test_every_haproxy_bind_is_a_path_to_its_default_backend(self):
        ha = sorted((p.front, p.back) for p in self.paths() if p.kind == "haproxy")
        self.assertEqual(ha, [("198.51.100.7:443", "10.9.9.9:443"), ("198.51.100.8:25", "192.0.2.48:12525"),
                              ("[2001:db8::7]:443", "10.9.9.9:443")])

    def test_a_tls_haproxy_front_names_its_http_host_from_the_certificate(self):
        self.assertEqual(D.haproxy_hosts(RAW), ["cm.example"])


class Inventory(unittest.TestCase):
    def test_a_fresh_inventory_is_read(self):
        self.assertEqual(D.inventory(exposure(), NOW), RAW)

    def test_a_stale_or_undated_or_empty_inventory_is_refused(self):
        for why, inv in {"stale": exposure(generated="2026-11-22T02:00:00Z"),
                         "undated": exposure(generated=""),
                         "no dnat": exposure(raw=RAW.replace("## dnat", "## nope"))}.items():
            with self.subTest(why=why), self.assertRaises(ValueError):
                D.inventory(inv, NOW)


class Plan(unittest.TestCase):
    """One scan target per BACKEND: the public front when the internet reaches it,
    else the LoadBalancer address (LAN-only exposure is still exposure)."""

    LB = [svc("mirror", ns="mirror", ip="192.0.2.49", ports=((873, "TCP"), (80, "TCP"))),
          svc("mail", ns="mail", ip="192.0.2.48", ports=((12525, "TCP"), (143, "TCP")))]

    def plan(self):
        return D.plan(self.LB, D.router_paths(RAW))

    def test_a_backend_reached_from_the_internet_is_scanned_at_its_public_front(self):
        self.assertEqual(self.plan()["mirror.mirror.svc"], ["192.0.2.49:80", "198.51.100.5:873"])
        self.assertEqual(self.plan()["mail.mail.svc"], ["192.0.2.48:143", "198.51.100.8:25"])

    def test_a_public_front_that_does_not_answer_falls_back_to_the_lb_address(self):
        # Found live 2026-11-23: preferring the front unconditionally dropped a backend
        # whose LB port answered but whose hairpinned front did not.
        up = {"192.0.2.49:873", "192.0.2.49:80"}
        self.assertEqual(D.plan(self.LB, D.router_paths(RAW), up)["mirror.mirror.svc"],
                         ["192.0.2.49:80", "192.0.2.49:873"])

    def test_the_candidates_are_every_public_front_and_the_lb_address(self):
        self.assertEqual(sorted(D.candidates(self.LB, D.router_paths(RAW))),
                         ["192.0.2.48:12525", "192.0.2.48:143", "192.0.2.49:80", "192.0.2.49:873",
                          "198.51.100.5:873", "198.51.100.6:40000", "198.51.100.6:40001",
                          "198.51.100.6:40002", "198.51.100.7:443", "198.51.100.8:25"])

    def test_router_backends_outside_any_service_get_a_line_of_their_own(self):
        got = self.plan()
        self.assertEqual(got["router-192.0.2.50"],
                         ["198.51.100.6:40000", "198.51.100.6:40001", "198.51.100.6:40002"])
        self.assertEqual(got["router-10.9.9.9"], ["198.51.100.7:443"])

    def test_a_v6_only_path_whose_backend_has_no_v4_route_is_not_planned(self):
        # 192.0.2.62:443 is reached only over NAT64; a pod has no IPv6, so it is
        # enumerated and reported unreachable, never silently dropped.
        self.assertNotIn("192.0.2.62", " ".join(sum(self.plan().values(), [])))

    def test_an_opted_out_service_stays_out_whichever_path_reaches_it(self):
        lb = self.LB + [svc("ingress", ns="kube-system", ip="192.0.2.50", skip="hosts scanned by name")]
        self.assertNotIn("router-192.0.2.50", D.plan(lb, D.router_paths(RAW)))
        self.assertNotIn("ingress.kube-system.svc", D.plan(lb, D.router_paths(RAW)))

    def test_lines_are_the_endpoints_file(self):
        lines = D.endpoint_lines({"b": ["192.0.2.1:2", "192.0.2.1:1"], "a": ["192.0.2.9:9"]})
        self.assertEqual(lines, ["a 192.0.2.9:9", "b 192.0.2.1:1,192.0.2.1:2"])


class Coverage(unittest.TestCase):
    def test_paths_enumerated_and_reachable_are_counted_per_kind(self):
        lb = [svc("mirror", ns="mirror", ip="192.0.2.49", ports=((873, "TCP"), (53, "UDP")))]
        paths = D.router_paths(RAW)
        plan = D.plan(lb, paths)
        reachable = {"198.51.100.5:873", "198.51.100.7:443"}
        line, unreached = D.coverage(["h.example"], lb, paths, plan, reachable)
        # 1 ingress + 2 lb + 7 dnat + 2 nat64 + 3 haproxy = 15; reachable: the host,
        # mirror:873 by lb + dnat + its nat64 twin, cm by 2 haproxy binds (v4 + v6 twin).
        self.assertEqual(line.split(";")[0], "coverage: 15 paths enumerated, 6 reachable")
        self.assertIn("udp 4 (no UDP scanner)", line)
        self.assertIn("haproxy 2/3", line)
        self.assertIn("nat64 1/2", line)
        self.assertIn("unreachable nat64 [2001:db8::6]:2443 -> 192.0.2.62:443", unreached)


class Starttls(unittest.TestCase):
    """infra-7rog.31.19: a plaintext port that upgrades to TLS gets a TLS check of its own.
    The protocol comes from the port a stranger connects to; the server name from DNS."""

    def test_only_starttls_ports_are_listed_with_their_protocol(self):
        planned = {"mail.mail.svc": ["192.0.2.48:143", "192.0.2.48:993", "198.51.100.8:25",
                                     "198.51.100.8:587", "198.51.100.8:4190"],
                   "mirror.mirror.svc": ["198.51.100.5:21", "198.51.100.5:873"]}
        names = {"mail.mail.svc": "mail.example", "mirror.mirror.svc": None}
        self.assertEqual(D.starttls_lines(planned, names), [
            "mail.mail.svc imap 192.0.2.48:143 mail.example",
            "mail.mail.svc sieve 198.51.100.8:4190 mail.example",
            "mail.mail.svc smtp 198.51.100.8:25 mail.example",
            "mail.mail.svc smtp 198.51.100.8:587 mail.example",
            "mirror.mirror.svc ftp 198.51.100.5:21 -",
        ])

    def test_the_name_is_the_forward_confirmed_ptr_of_a_public_address_of_the_line(self):
        dns = {"ptr": {"198.51.100.8": "mail.example", "192.168.0.48": "mail.lan.example",
                       "198.51.100.9": "liar.example"},
               "a": {"mail.example": ["198.51.100.8"], "mail.lan.example": ["192.168.0.48"],
                     "liar.example": ["203.0.113.1"]}}
        with mock.patch.object(D, "ptr", side_effect=lambda ip: dns["ptr"].get(ip)), \
             mock.patch.object(D, "forward", side_effect=lambda n: dns["a"].get(n, [])):
            # A private address's own PTR is a LAN name the certificate does not carry.
            self.assertEqual(D.server_name(["192.168.0.48:143", "198.51.100.8:25"]), "mail.example")
            self.assertIsNone(D.server_name(["192.168.0.48:143"]))
            # A PTR whose name does not resolve back to the address is not believed.
            self.assertIsNone(D.server_name(["198.51.100.9:25"]))

    def test_a_lan_only_service_declares_its_certificate_name(self):
        # dolt-lb (2026-11-24): no public front, no PTR -- testssl checked the cert against
        # the bare IP and reported a false HIGH "does not match". The Service states the
        # name its certificate carries; it wins only when it resolves to the Service's IP.
        named = svc("dolt-lb", ns="backlog", ip="192.168.0.63", ports=((3306, "TCP"),))
        named["metadata"]["annotations"]["dast.mdapi.ch/server-name"] = "dolt.lan.example"
        liar = svc("x", ns="y", ip="192.168.0.64", ports=((3306, "TCP"),))
        liar["metadata"]["annotations"]["dast.mdapi.ch/server-name"] = "elsewhere.example"
        a = {"dolt.lan.example": ["192.168.0.63"], "elsewhere.example": ["203.0.113.9"]}
        with mock.patch.object(D, "forward", side_effect=lambda n: a.get(n, [])):
            self.assertEqual(D.declared_names([named, liar, svc("plain")]),
                             {"dolt-lb.backlog.svc": "dolt.lan.example"})

    def test_a_declared_name_wins_over_the_derived_one(self):
        planned = {"dolt-lb.backlog.svc": ["192.168.0.63:3306"]}
        with mock.patch.object(D, "server_name", return_value=None):
            self.assertEqual(D.listener_names(planned, {"dolt-lb.backlog.svc": "dolt.lan.example"}),
                             {"dolt-lb.backlog.svc": "dolt.lan.example"})
        with mock.patch.object(D, "server_name", return_value="derived.example"):
            self.assertEqual(D.listener_names({"a.b.svc": ["198.51.100.8:25"]}, {}),
                             {"a.b.svc": "derived.example"})

    def test_the_resolver_seams_tolerate_a_missing_record(self):
        with mock.patch.object(D.socket, "gethostbyaddr", side_effect=OSError), \
             mock.patch.object(D.socket, "gethostbyname_ex", side_effect=OSError):
            self.assertIsNone(D.ptr("198.51.100.8"))
            self.assertEqual(D.forward("mail.example"), [])
        with mock.patch.object(D.socket, "gethostbyaddr", return_value=("mail.example", [], [])), \
             mock.patch.object(D.socket, "gethostbyname_ex", return_value=("m", [], ["198.51.100.8"])):
            self.assertEqual(D.ptr("198.51.100.8"), "mail.example")
            self.assertEqual(D.forward("mail.example"), ["198.51.100.8"])


class Probe(unittest.TestCase):
    def test_reachable_keeps_only_the_ports_that_answer(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        open_ = f"127.0.0.1:{srv.getsockname()[1]}"
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        dead = f"127.0.0.1:{closed.getsockname()[1]}"
        closed.close()
        try:
            self.assertEqual(D.reachable([open_, dead], timeout=1), {open_})
        finally:
            srv.close()


class Run(unittest.TestCase):
    def run_with(self, ingresses, services=(svc("lb"),), inv=None, day=0, up=None):
        written = {}
        lists = {D.INGRESSES: list(ingresses), D.SERVICES: list(services)}
        inv = exposure() if inv is None else inv
        with mock.patch.object(D, "list_items", side_effect=lambda path: lists[path]), \
             mock.patch.object(D, "read_exposure", return_value=inv), \
             mock.patch.object(D, "now", return_value=NOW), \
             mock.patch.object(D, "reachable", side_effect=lambda ts: set(ts) if up is None else up), \
             mock.patch.object(D, "write_lines", side_effect=lambda path, ls: written.setdefault(path, ls)), \
             mock.patch.object(D, "today", return_value=day), \
             mock.patch.object(D, "server_name", return_value=None):
            rc = D.run()
        return rc, written

    def test_starttls_uses_the_declared_name_of_a_lan_only_service(self):
        dolt = svc("dolt-lb", ns="backlog", ip="192.0.2.63", ports=((3306, "TCP"),))
        dolt["metadata"]["annotations"]["dast.mdapi.ch/server-name"] = "dolt.example"
        with mock.patch.object(D, "forward", side_effect=lambda n: ["192.0.2.63"] if n == "dolt.example" else []):
            _, written = self.run_with([ing("a.example")], services=[dolt])
        self.assertIn("dolt-lb.backlog.svc mysql 192.0.2.63:3306 dolt.example", written[D.STARTTLS])

    def test_starttls_targets_are_written_from_the_reachable_endpoints(self):
        lb = [svc("mail", ns="mail", ip="192.0.2.48", ports=((143, "TCP"), (993, "TCP")))]
        _, written = self.run_with([ing("a.example")], services=lb)
        self.assertEqual(written[D.STARTTLS], ["mail.mail.svc imap 192.0.2.48:143 -",
                                               "mail.mail.svc smtp 198.51.100.8:25 -"])

    def test_writes_todays_host_share_and_every_reachable_endpoint(self):
        hosts = [f"h{i}.example" for i in range(30)]
        rc, written = self.run_with([ing(*hosts)], day=1)
        self.assertEqual(rc, 0)
        # cm.example comes from the haproxy TLS front, not from any file in this repo.
        self.assertEqual(written[D.OUT], D.todays_share(sorted(hosts + ["cm.example"]), 1))
        self.assertIn("lb.app.svc 192.0.2.7:443", written[D.ENDPOINTS])
        self.assertIn("router-192.0.2.49 198.51.100.5:873", written[D.ENDPOINTS])

    def test_unreachable_addresses_are_not_handed_to_the_scanner(self):
        rc, written = self.run_with([ing("a.example")], up={"192.0.2.7:443"})
        self.assertEqual((rc, written[D.ENDPOINTS]), (0, ["lb.app.svc 192.0.2.7:443"]))

    def test_every_candidate_is_probed_before_the_plan_is_made(self):
        probed = []
        lists = {D.INGRESSES: [ing("a.example")], D.SERVICES: [svc("lb")]}
        with mock.patch.object(D, "list_items", side_effect=lambda path: lists[path]), \
             mock.patch.object(D, "read_exposure", return_value=exposure()), \
             mock.patch.object(D, "now", return_value=NOW), \
             mock.patch.object(D, "reachable", side_effect=lambda ts: probed.extend(ts) or set()), \
             mock.patch.object(D, "write_lines"):
            D.run()
        self.assertIn("192.0.2.7:443", probed)
        self.assertIn("198.51.100.5:873", probed)

    def test_a_new_router_path_is_picked_up_with_no_commit(self):
        # The promise of infra-7rog.31.9: a DNAT added on the router reaches the scan
        # through the carried inventory alone.
        extra = RAW.replace("## haproxy", "firewall.@redirect[9]=redirect\n"
                            "firewall.@redirect[9].src='wan'\nfirewall.@redirect[9].src_dip='198.51.100.9'\n"
                            "firewall.@redirect[9].src_dport='8443'\nfirewall.@redirect[9].dest_ip='192.0.2.77'\n"
                            "## haproxy")
        _, written = self.run_with([ing("a.example")], inv=exposure(raw=extra))
        self.assertIn("router-192.0.2.77 198.51.100.9:8443", written[D.ENDPOINTS])

    def test_no_hosts_fails_and_writes_nothing(self):
        no_tls = RAW.replace(" crt /etc/ssl/acme/cm.example.combined.crt", "")
        rc, written = self.run_with([], inv=exposure(raw=no_tls))
        self.assertEqual((rc, written), (1, {}))

    def test_no_endpoints_fails_and_writes_nothing(self):
        rc, written = self.run_with([ing("a.example")], up=set())
        self.assertEqual((rc, written), (1, {}))

    def test_an_unusable_router_inventory_fails_and_writes_nothing(self):
        rc, written = self.run_with([ing("a.example")], inv=exposure(generated="2026-11-01T00:00:00Z"))
        self.assertEqual((rc, written), (1, {}))


class ReadExposure(unittest.TestCase):
    def test_reads_the_mounted_configmap_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "raw").write_text(RAW)
            Path(d, "generated").write_text("2026-11-23T03:17:00Z\n")
            with mock.patch.object(D, "EXPOSURE", d):
                self.assertEqual(D.read_exposure(), exposure())


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
        reads = {"zap": "/targets/hosts", "nuclei": "/targets/hosts", "nuclei-net": "/targets/endpoints",
                 "testssl": "/targets/starttls"}
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

    def test_testssl_runs_as_a_numeric_non_root_user_on_a_read_only_root(self):
        (t,) = [c for c in self.pod["containers"] if c["name"] == "testssl"]
        sc = t["securityContext"]
        self.assertIsInstance(sc["runAsUser"], int)
        self.assertNotEqual(sc["runAsUser"], 0)
        self.assertTrue(sc["readOnlyRootFilesystem"])
        self.assertIn("@sha256:", t["image"])
        # testssl's temp dir is `mktemp -d /tmp/testssl.XXXXXX`, falling back to $PWD: under a
        # read-only root it needs a writable /tmp and a working directory that is not `/`.
        mounts = {m["mountPath"] for m in t["volumeMounts"]}
        self.assertIn("/tmp", mounts)
        self.assertNotIn(t.get("workingDir", "/"), ("", "/"))

    def test_nuclei_containers_do_not_share_a_template_home(self):
        homes = [m["name"] for c in self.pod["containers"] if c["name"].startswith("nuclei")
                 for m in c["volumeMounts"] if m["mountPath"].startswith("/home/")]
        self.assertEqual(len(homes), len(set(homes)), homes)

    def test_discovery_reads_the_router_inventory_and_no_static_host_list(self):
        (init,) = self.pod["initContainers"]
        mounts = {m["mountPath"]: m["name"] for m in init["volumeMounts"]}
        vols = {v["name"]: v for v in self.pod["volumes"]}
        self.assertEqual(vols[mounts[D.EXPOSURE]]["configMap"]["name"], "dast-exposure")
        self.assertFalse(vols[mounts[D.EXPOSURE]]["configMap"].get("optional"))
        self.assertNotIn("dast-targets", {v.get("configMap", {}).get("name") for v in self.pod["volumes"]})
        self.assertFalse((Path(repo_root()) / "dast" / "targets.yml").exists())

    def test_the_scan_may_reach_every_tcp_port_of_the_public_fronts(self):
        # Router paths are scanned at the public address (hairpin through bpi-r4),
        # so egress to the static v4 block must allow any TCP port -- and only TCP.
        docs = list(yaml.safe_load_all((Path(repo_root()) / "dast" / "netpol.yml").read_text()))
        (pol,) = [d for d in docs if d["metadata"]["name"] == "edge-dast-egress"]
        rules = [r for r in pol["spec"]["egress"]
                 if any(t.get("ipBlock", {}).get("cidr") == "31.3.128.48/28" for t in r["to"])]
        self.assertEqual([r["ports"] for r in rules], [[{"protocol": "TCP"}]])

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
