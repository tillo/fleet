#!/usr/bin/env python3
"""
Configure a NextDNS-independent DNS fallback on Technitium, as a
Conditional Forwarder zone for "." with priority-tiered FWD records:

  Priority 1: 45.90.28.16  (Udp)  — NextDNS Anycast (primary, parallel)
  Priority 1: 45.90.30.16  (Udp)  — NextDNS Anycast (primary, parallel)
  Priority 2: this-server  (Udp)  — this server's own recursion (root hints)
  Priority 3: 9.9.9.9      (Udp)  — Quad9, last resort

Technitium prefers lower-numbered priorities; records sharing a priority are
queried together. Filtering stays the normal path: pri-1 answers almost
everything. The fallback tiers only engage when the NextDNS pair stops
answering — which is exactly what happened on 2026-10-08.

⚠️ THE GLOBAL FORWARDERS LIST MUST BE EMPTY, and this script enforces that.
This is the whole point of the file, and getting it wrong is silent.

    `this-server` does NOT mean "recurse from root hints". It means "resolve
    this the way this server resolves by default" — and the default is
    settings.forwarders WHEN THAT LIST IS NON-EMPTY. So with the global
    forwarders set, pri-2 quietly re-enters the SAME NextDNS dependency it is
    supposed to replace: a fallback that fails whenever the primary fails.

    Proven 2026-10-08 on this deployment by A/B: the same `this-server` tier,
    with the global list flipped between the two states, answering differently.

        settings.forwarders = (empty)     -> NXDOMAIN   (real iteration ran)
        settings.forwarders = 192.0.2.1   -> SERVFAIL   (it re-entered the list)
        settings.forwarders = (empty)     -> NXDOMAIN   (recursion again)

    Each phase used its own CF zone and its own query name, on purpose: see
    the trap below, and note that NXDOMAIN is negatively cached, so re-using
    one query name makes the A/B prove nothing.

    ⛔ TRAP, and the reason an earlier version of this note was wrong: do NOT
    run this probe with the queried name INSIDE the CF zone under test.
    `this-server` recursion for such a name re-enters its own forwarder and
    fails with an error that blames the network:

        DnsClientNoResponseException: ... no valid response from name servers
          [b.root-servers.net ... e.root-servers.net] at delegation .

    That "all 13 root servers failed" is a false alarm — the roots answer this
    estate fine, from the jump pod and from inside the Technitium pod alike.
    Use a zone whose parent really resolves but whose queried name does not
    exist, and a fresh name each time.

    An earlier version of this file kept the global forwarder hostname
    deliberately, calling it "defense in depth if the '.' CF is ever
    deleted". That reasoning was backwards: the list was already dead code
    for ordinary queries (the "." CF matches everything), so all it could do
    was sabotage pri-2. If the "." CF IS ever deleted, resolution falls back
    to plain root recursion — which is what we want anyway.

DO NOT add a hostname (e.g. technitium-d7253f.dns.nextdns.io) as a FWD
inside this CF: resolving the hostname re-enters the same "." CF and the
whole resolver deadlocks. Use the anycast IPs.

The more-specific CF zones (mdapi.ch, tillo.ch, etc.) and Primary zones
(home.tillo.ch) still match before "." so they're not affected.

NOTE ON PRIORITY RECONCILIATION: `/api/zones/records/update` renames a
forwarder but silently keeps its OLD priority (verified 2026-10-08: passing
newForwarder + newForwarderPriority="3" changed the forwarder and left
priority at 0). So a wrong priority is fixed by delete + re-add, not update.
The delete costs about a second, during which the sibling pri-1 record and
the pri-2/pri-3 tiers still answer.

Idempotent.
"""
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request

KCTX = "mdapi-prod-direct"
NAMESPACE = "technitium"

ROOT_ZONE = "."

# (forwarder, protocol, priority) — the DESIRED state, reconciled below.
FWDS = [
    # REMOVED hostname forwarder — causes resolution loop inside . CF
    ("45.90.28.16",                      "Udp", 1),
    ("45.90.30.16",                      "Udp", 1),
    ("this-server",                      "Udp", 2),
    ("9.9.9.9",                          "Udp", 3),
]

PODS = [
    ("primary",   "TECHNITIUM_API_PRIMARY_TOKEN",   "technitium-primary",   18550),
    ("secondary", "TECHNITIUM_API_SECONDARY_TOKEN", "technitium-secondary", 18553),
]


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          check=True).stdout


def get_token(key):
    out = sh(f"kubectl --context {KCTX} -n {NAMESPACE} get secret "
             f"technitium-exporter-tokens -o jsonpath='{{.data.{key}}}'")
    return base64.b64decode(out.strip()).decode()


def http_get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def api(base, path, token, **params):
    params["token"] = token
    qs = urllib.parse.urlencode(params)
    return http_get(f"{base}{path}?{qs}")


def list_zones(base, token):
    j = api(base, "/api/zones/list", token)
    return {z["name"]: z for z in j.get("response", {}).get("zones", [])}


def zone_records(base, token, zone):
    j = api(base, "/api/zones/records/get", token, zone=zone, domain=zone)
    return j.get("response", {}).get("records", [])


def current_fwds(base, token, zone):
    """forwarder -> (priority, rData) for every FWD at the zone apex."""
    out = {}
    for r in zone_records(base, token, zone):
        if r.get("type") != "FWD":
            continue
        rd = r.get("rData", {})
        pr = rd.get("forwarderPriority", rd.get("priority"))
        out[rd.get("forwarder")] = (pr, rd)
    return out


def create_cf_zone(base, token, zone, forwarder, protocol):
    return api(base, "/api/zones/create", token, zone=zone, type="Forwarder",
               initializeForwarder="true", forwarder=forwarder,
               protocol=protocol)


def add_fwd_record(base, token, zone, forwarder, protocol, priority):
    return api(base, "/api/zones/records/add", token, zone=zone, domain=zone,
               type="FWD", forwarder=forwarder, protocol=protocol,
               forwarderPriority=str(priority), ttl="300")


def del_fwd_record(base, token, zone, forwarder, protocol, priority):
    return api(base, "/api/zones/records/delete", token, zone=zone, domain=zone,
               type="FWD", forwarder=forwarder, protocol=protocol,
               forwarderPriority=str(priority))


def ensure_no_global_forwarders(base, token, label):
    """The actual fix for pri-2 — see the module docstring."""
    s = api(base, "/api/settings/get", token).get("response", {})
    fw = s.get("forwarders")
    if not fw:
        print(f"  global forwarders: already empty (pri-2 can recurse)")
        return True
    print(f"  global forwarders: {fw} -> clearing (this is what broke pri-2)")
    r = api(base, "/api/settings/set", token, forwarders="")
    if r.get("status") != "ok":
        print(f"    ⚠️  set failed: {r.get('status')} {r.get('errorMessage','')}")
        return False
    s = api(base, "/api/settings/get", token).get("response", {})
    fw2 = s.get("forwarders")
    print(f"  global forwarders now: {fw2!r}")
    if fw2:
        print("    ⚠️  still non-empty — pri-2 remains broken on this replica")
        return False
    # Clearing forwarders must not disturb anything else.
    for k, want in (("recursion", "AllowOnlyForPrivateNetworks"),
                    ("dnssecValidation", True)):
        if k in s and s[k] != want:
            print(f"    ⚠️  setting {k} changed to {s[k]!r} (wanted {want!r})")
    return True


def reconcile_zone(base, token):
    zones = list_zones(base, token)
    root_exists = "" in zones or ROOT_ZONE in zones

    if not root_exists:
        f0, p0, pr0 = FWDS[0]
        print(f"  CREATE root zone with FWD {f0} ({p0}, pri={pr0})")
        r = create_cf_zone(base, token, ROOT_ZONE, f0, p0)
        print(f"    create: {r.get('status')} {r.get('errorMessage','')}")
    else:
        root = zones.get("", zones.get(ROOT_ZONE))
        if root.get("type") != "Forwarder":
            print(f"  ABORT: root zone exists with type={root.get('type')} "
                  f"(not Forwarder).")
            return False
        print("  root zone: exists as Forwarder")

    for f, p, pr in FWDS:
        cur = current_fwds(base, token, ROOT_ZONE)
        if f not in cur:
            print(f"    ADD    {f:14s} ({p}, pri={pr})")
            r = add_fwd_record(base, token, ROOT_ZONE, f, p, pr)
            print(f"      add: {r.get('status')} {r.get('errorMessage','')}")
            continue
        cur_pr, cur_rd = cur[f]
        if str(cur_pr) == str(pr):
            print(f"    OK     {f:14s} pri={pr} (already set)")
            continue
        if str(cur_rd.get("protocol", p)) != p:
            print(f"    SKIP   {f:14s} protocol={cur_rd.get('protocol')} "
                  f"(want {p}) — manual fix")
            continue
        # update() would silently keep the old priority — delete + re-add.
        print(f"    FIX    {f:14s} pri={cur_pr} -> {pr} (delete + re-add; "
              f"update keeps the old priority)")
        r = del_fwd_record(base, token, ROOT_ZONE, f, p, cur_pr)
        print(f"      del: {r.get('status')} {r.get('errorMessage','')}")
        r = add_fwd_record(base, token, ROOT_ZONE, f, p, pr)
        print(f"      add: {r.get('status')} {r.get('errorMessage','')}")

    print("  final tiers:")
    for f, (pr, rd) in sorted(current_fwds(base, token, ROOT_ZONE).items(),
                              key=lambda kv: str(kv[1][0])):
        print(f"    pri={pr}  {f}  ({rd.get('protocol')})")
    return True


def main():
    for label, secret_key, svc, port in PODS:
        print(f"\n=== {label} (svc/{svc} -> 127.0.0.1:{port}) ===")
        token = get_token(secret_key)
        pf = subprocess.Popen(
            ["kubectl", "--context", KCTX, "-n", NAMESPACE,
             "port-forward", f"svc/{svc}", f"{port}:5380"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2.5)
        try:
            base = f"http://127.0.0.1:{port}"
            # Order matters: make recursion real FIRST, so the delete+re-add
            # below always has a working backstop beneath it.
            ensure_no_global_forwarders(base, token, label)
            reconcile_zone(base, token)
        finally:
            pf.terminate()
            pf.wait(timeout=5)


if __name__ == "__main__":
    main()
