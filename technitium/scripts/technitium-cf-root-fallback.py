#!/usr/bin/env python3
"""
Configure recursion fallback via a Conditional Forwarder zone for "."
with priority-tiered FWD records:

  Priority 1: 45.90.28.16  (Udp)  — NextDNS Anycast (primary)
  Priority 1: 45.90.30.16  (Udp)  — NextDNS Anycast (parallel)
  Priority 2: this-server  (Udp)  — recursive root hints (fallback)

Technitium prefers lower-numbered priorities. When both pri=1 IPs fail
to answer within forwarderTimeout, the pri=2 "this-server" record kicks
in and the lookup recurses from the built-in root hints — so an entire
NextDNS outage degrades to "recursing from roots, slightly slower",
not SERVFAIL.

DO NOT add a hostname (e.g. technitium-d7253f.dns.nextdns.io) as a FWD
inside this CF: resolving the hostname re-enters the same "." CF and
the whole resolver deadlocks. The original global forwarders setting
keeps that hostname but ONLY for queries that don't match this "." CF
(none in practice — see "Note on global forwarders" below).

The more-specific CF zones (mdapi.ch, tillo.ch, etc.) and Primary zones
(home.tillo.ch) still match before "." so they're not affected.

Note on global forwarders: the existing settings.forwarders list still
holds the NextDNS hostname + IPs. Once the "." CF exists, that list is
effectively dead code (CF wins on every query). Left in place as a
defense-in-depth fallback if the "." CF is ever deleted.

Idempotent.
"""
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request

KCTX = "mdapi-prod"
NAMESPACE = "technitium"

ROOT_ZONE = "."

# (forwarder, protocol, priority)
FWDS = [
    # REMOVED hostname forwarder — causes resolution loop inside . CF
    ("45.90.28.16",                      "Udp", 1),
    ("45.90.30.16",                      "Udp", 1),
    ("this-server",                      "Udp", 2),
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


def list_zones(base, token):
    j = http_get(f"{base}/api/zones/list?token={token}")
    return {z["name"]: z for z in j.get("response", {}).get("zones", [])}


def zone_records(base, token, zone):
    qs = urllib.parse.urlencode({"token": token, "zone": zone, "domain": zone})
    j = http_get(f"{base}/api/zones/records/get?{qs}")
    return j.get("response", {}).get("records", [])


def create_cf_zone(base, token, zone, forwarder, protocol):
    qs = urllib.parse.urlencode({
        "token": token,
        "zone": zone,
        "type": "Forwarder",
        "initializeForwarder": "true",
        "forwarder": forwarder,
        "protocol": protocol,
    })
    return http_get(f"{base}/api/zones/create?{qs}")


def add_fwd_record(base, token, zone, domain, forwarder, protocol, priority):
    qs = urllib.parse.urlencode({
        "token": token,
        "zone": zone,
        "domain": domain,
        "type": "FWD",
        "forwarder": forwarder,
        "protocol": protocol,
        "forwarderPriority": str(priority),
        "ttl": "300",
    })
    return http_get(f"{base}/api/zones/records/add?{qs}")


def main():
    for label, secret_key, svc, port in PODS:
        token = get_token(secret_key)
        print(f"\n=== {label} (svc/{svc} -> 127.0.0.1:{port}) ===")
        pf = subprocess.Popen(
            ["kubectl", "--context", KCTX, "-n", NAMESPACE,
             "port-forward", f"svc/{svc}", f"{port}:5380"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2.5)
        try:
            base = f"http://127.0.0.1:{port}"
            zones = list_zones(base, token)
            root_apex = "" if "" in zones else ROOT_ZONE
            # Technitium lists root as empty string or "."; probe both
            root_exists = "" in zones or ROOT_ZONE in zones

            if not root_exists:
                # Create with the highest-priority FWD as initializer
                f0, p0, pr0 = FWDS[0]
                print(f"  CREATE root zone with FWD {f0} ({p0}, pri={pr0})")
                r = create_cf_zone(base, token, ROOT_ZONE, f0, p0)
                print(f"    create: {r.get('status')} {r.get('errorMessage','')}")
                remaining = FWDS[1:]
                # Patch priority on the auto-created record (initializeForwarder
                # creates a single FWD but priority defaults to 0 — we want 1)
                # Treat as add-or-update via /records/update after the fact.
            else:
                t = zones.get("", zones.get(ROOT_ZONE)).get("type")
                if t != "Forwarder":
                    print(f"  ABORT: root zone exists with type={t} (not Forwarder).")
                    continue
                print(f"  EXISTS root as Forwarder zone")
                remaining = FWDS  # add all; we'll dedupe below

            # Look at current FWD records and add anything missing
            recs = zone_records(base, token, ROOT_ZONE)
            current_fwds = {
                r.get("rData", {}).get("forwarder"): r
                for r in recs if r.get("type") == "FWD"
            }
            print(f"  current FWDs at apex: {list(current_fwds.keys()) or '(none)'}")
            for f, p, pr in FWDS:
                if f in current_fwds:
                    cur = current_fwds[f].get("rData", {})
                    cur_pr = cur.get("forwarderPriority", cur.get("priority"))
                    if str(cur_pr) == str(pr):
                        print(f"    OK     {f:38s} pri={pr} (already set)")
                        continue
                    print(f"    SKIP   {f:38s} exists with pri={cur_pr} (want {pr}) — manual fix")
                    continue
                print(f"    ADD    {f:38s} ({p}, pri={pr})")
                r = add_fwd_record(base, token, ROOT_ZONE, ROOT_ZONE, f, p, pr)
                print(f"      add: {r.get('status')} {r.get('errorMessage','')}")
        finally:
            pf.terminate()
            pf.wait(timeout=5)


if __name__ == "__main__":
    main()
