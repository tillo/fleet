#!/usr/bin/env python3
"""
Add Conditional Forwarder zones to Technitium primary + secondary for the
6 zones currently hosted on ns.mdapi.ch (31.3.128.59 / 2a0d:d05:401:e900::b).

Internal clients hitting Technitium for these zones bypass NextDNS and get
the authoritative answer directly from ns.mdapi.ch — faster + bypasses any
upstream filtering, and prepares for the BPI-R4 split-horizon plan where
the FWD address will later point to BPI-R4 instead.

Idempotent: creates zone only if missing, skips if FWD already points to
ns.mdapi.ch.
"""
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request

KCTX = "mdapi-prod"
NAMESPACE = "technitium"
NS_MDAPI_V4 = "31.3.128.59"
NS_MDAPI_V6 = "2a0d:d05:401:e900::b"

# Zones currently authoritative on ns.mdapi.ch. envuassu.ch (Infomaniak),
# textopolis.net (decommissioned), plex.direct (vendor) intentionally excluded.
CF_ZONES = [
    "mdapi.ch",
    "tillo.ch",
    "coiffuredreams.ch",
    "dellambrogio.ch",
    "lithia.eu",
    "coders.ch",
]

PODS = [
    ("primary",   "TECHNITIUM_API_PRIMARY_TOKEN",   "technitium-primary",   18550),
    ("secondary", "TECHNITIUM_API_SECONDARY_TOKEN", "technitium-secondary", 18553),
]


def sh(cmd, check=True, capture=True):
    r = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if check and r.returncode:
        raise RuntimeError(f"cmd failed: {cmd}\n{r.stderr}")
    return r.stdout


def get_token(key):
    out = sh(f"kubectl --context {KCTX} -n {NAMESPACE} get secret "
             f"technitium-exporter-tokens -o jsonpath='{{.data.{key}}}'")
    return base64.b64decode(out.strip()).decode()


def http_get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def list_zones(base, token):
    j = http_get(f"{base}/api/zones/list?token={token}")
    return {z["name"]: z for z in j.get("response", {}).get("zones", [])}


def zone_records(base, token, zone):
    qs = urllib.parse.urlencode({"token": token, "zone": zone, "domain": zone})
    j = http_get(f"{base}/api/zones/records/get?{qs}")
    return j.get("response", {}).get("records", [])


def create_cf_zone(base, token, zone, forwarder, protocol="Udp"):
    qs = urllib.parse.urlencode({
        "token": token,
        "zone": zone,
        "type": "Forwarder",
        "initializeForwarder": "true",
        "forwarder": forwarder,
        "protocol": protocol,
    })
    j = http_get(f"{base}/api/zones/create?{qs}")
    return j


def add_fwd_record(base, token, zone, domain, forwarder, protocol="Udp",
                   priority=1):
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
    j = http_get(f"{base}/api/zones/records/add?{qs}")
    return j


def main():
    for label, secret_key, svc, port in PODS:
        token = get_token(secret_key)
        # Port-forward in background
        print(f"\n=== {label} (svc/{svc} -> 127.0.0.1:{port}) ===")
        pf = subprocess.Popen(
            ["kubectl", "--context", KCTX, "-n", NAMESPACE,
             "port-forward", f"svc/{svc}", f"{port}:5380"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2.5)
        try:
            base = f"http://127.0.0.1:{port}"
            existing = list_zones(base, token)
            for z in CF_ZONES:
                if z in existing:
                    et = existing[z].get("type")
                    if et == "Forwarder":
                        # Check FWD record content
                        recs = zone_records(base, token, z)
                        fwds = [r for r in recs if r.get("type") == "FWD"]
                        targets = [r.get("rData", {}).get("forwarder") for r in fwds]
                        if NS_MDAPI_V4 in targets:
                            print(f"  SKIP {z:24s} (CF exists, FWD -> {targets})")
                            continue
                        print(f"  PATCH {z:24s} (CF exists but FWD missing -> add)")
                        r = add_fwd_record(base, token, z, z, NS_MDAPI_V4)
                        print(f"    add: {r.get('status')}")
                    else:
                        print(f"  WARN {z:24s} exists as type={et} — leaving untouched")
                    continue
                print(f"  CREATE {z:24s} as Forwarder -> {NS_MDAPI_V4} (Udp)")
                r = create_cf_zone(base, token, z, NS_MDAPI_V4)
                print(f"    create: {r.get('status')}")
                if r.get("status") != "ok":
                    print(f"    response: {r}")
        finally:
            pf.terminate()
            pf.wait(timeout=5)


if __name__ == "__main__":
    main()
