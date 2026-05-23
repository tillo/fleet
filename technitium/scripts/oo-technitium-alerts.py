#!/usr/bin/env python3
"""
Set up OpenObserve scheduled alerts on the technitium_queries stream:

  Alert 1 — technitium_client_qps_spike:
    Any single client IP doing > 1000 queries in last 5 minutes (sustained
    >200 qpm). Fires once, silences for 60 min.

  Alert 2 — technitium_client_nxdomain_surge:
    Any client whose NXDOMAIN ratio exceeds 50% over 15 minutes with at
    least 100 queries (filters out 1-query flukes). Catches misconfigured
    apps, malware DGA, or DNS exfil patterns.

Both fire to the existing Pushover account (same creds the Alertmanager
mdapi receiver uses).

Idempotent: re-creates template / destination / alerts via PUT if missing,
no-op if already at the desired shape.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

OO_HOST = "127.0.0.1"
OO_PORT = 15080
ORG = "default"


def _akeyless(name):
    """Fetch a secret from Akeyless via the akeyless CLI on mbptillo (this
    machine doesn't carry akeyless credentials). All callers in this module
    go through here, so there's a single point to change if the proxy moves."""
    return subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/tillo@exion.id_rsa"), "mbptillo",
         f"akeyless get-secret-value --name {name}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


PUSHOVER_TOKEN = _akeyless("/mdapi/pushover/mdapi-alertmanager-token")
PUSHOVER_USER = _akeyless("/mdapi/pushover/user-key")

TEMPLATE_NAME = "pushover_mdapi"
DESTINATION_NAME = "pushover_mdapi"

ALERTS = [
    {
        "name": "technitium_client_qps_spike",
        "description": (
            "A single LAN client did >1000 queries in 5min — sustained "
            ">200 qpm is anomalously high and worth investigating "
            "(misbehaving app, scanner, exfil, or new heavy service)."
        ),
        "trigger_condition": {
            "period": 5,
            "operator": ">=",
            "threshold": 1,
            "frequency": 5,
            "frequency_type": "minutes",
            "silence": 60,
            "tolerance_in_secs": 0,
        },
        "query_condition": {
            "type": "sql",
            "sql": (
                "SELECT clientipaddress, count(*) AS qpm "
                'FROM "technitium_queries" '
                "GROUP BY clientipaddress "
                "HAVING count(*) > 1000"
            ),
        },
    },
    {
        "name": "technitium_client_nxdomain_surge",
        "description": (
            "A single LAN client has >50% NXDOMAIN ratio over 15 min "
            "(>=100 queries). Indicates misconfigured DNS, a DGA-style "
            "malware beacon, or a brute-force domain probe."
        ),
        "trigger_condition": {
            "period": 15,
            "operator": ">=",
            "threshold": 1,
            "frequency": 5,
            "frequency_type": "minutes",
            "silence": 120,
            "tolerance_in_secs": 0,
        },
        "query_condition": {
            "type": "sql",
            "sql": (
                "SELECT clientipaddress, "
                "       count(*) AS total, "
                "       sum(CASE WHEN rcode='NxDomain' THEN 1 ELSE 0 END) AS nx, "
                "       cast(sum(CASE WHEN rcode='NxDomain' THEN 1 ELSE 0 END) AS DOUBLE) "
                "         / cast(count(*) AS DOUBLE) AS nx_ratio "
                'FROM "technitium_queries" '
                "GROUP BY clientipaddress "
                "HAVING count(*) >= 100 "
                "   AND cast(sum(CASE WHEN rcode='NxDomain' THEN 1 ELSE 0 END) AS DOUBLE) "
                "         / cast(count(*) AS DOUBLE) > 0.5"
            ),
        },
    },
]


def get_auth():
    pw = _akeyless("/mdapi/openobserve/o2/root-password")
    return base64.b64encode(f"tillo@tillo.ch:{pw}".encode()).decode()


def req(method, path, body=None, auth=None):
    url = f"http://{OO_HOST}:{OO_PORT}/api{path}"
    headers = {"Authorization": f"Basic {auth}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def ensure_template(auth):
    body_str = json.dumps({
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": "[OO] {alert_name}",
        "message": (
            "{alert_name} fired on stream {stream_name}\n"
            "Threshold: {alert_operator} {alert_threshold}\n"
            "Matched: {alert_count} rows (period {alert_period}m)\n"
            "Org: {org_name}"
        ),
        "url": "https://logs.mdapi.ch/web/alerts/alertsList?org_identifier=default",
        "url_title": "Open in OpenObserve",
    })
    payload = {"name": TEMPLATE_NAME, "body": body_str, "type": "http"}
    code, r = req("POST", f"/{ORG}/alerts/templates", payload, auth)
    if code == 200:
        print(f"  template: created/replaced {TEMPLATE_NAME}")
        return
    # Already exists? Try PUT
    code, r = req("PUT", f"/{ORG}/alerts/templates/{TEMPLATE_NAME}", payload, auth)
    print(f"  template: PUT -> {code} {r}")


def ensure_destination(auth):
    payload = {
        "name": DESTINATION_NAME,
        "url": "https://api.pushover.net/1/messages.json",
        "method": "post",
        "headers": {"Content-Type": "application/json"},
        "template": TEMPLATE_NAME,
        "skip_tls_verify": False,
    }
    code, r = req("POST", f"/{ORG}/alerts/destinations", payload, auth)
    if code == 200:
        print(f"  destination: created/replaced {DESTINATION_NAME}")
        return
    code, r = req("PUT", f"/{ORG}/alerts/destinations/{DESTINATION_NAME}", payload, auth)
    print(f"  destination: PUT -> {code} {r}")


def ensure_alert(auth, spec):
    payload = {
        "name": spec["name"],
        "description": spec["description"],
        "stream_name": "technitium_queries",
        "stream_type": "logs",
        "destinations": [DESTINATION_NAME],
        "enabled": True,
        "is_real_time": False,
        "trigger_condition": spec["trigger_condition"],
        "query_condition": spec["query_condition"],
    }
    code, r = req("POST", f"/v2/{ORG}/alerts", payload, auth)
    if code == 200:
        print(f"  alert: created {spec['name']}")
        return
    # Maybe exists — try update via PUT-by-name or list+match
    code_get, alerts = req("GET", f"/v2/{ORG}/alerts", None, auth)
    existing_id = None
    if code_get == 200:
        for a in (alerts.get("list") or []):
            if a.get("name") == spec["name"]:
                existing_id = a.get("alert_id") or a.get("id")
                break
    if existing_id:
        code, r = req("PUT", f"/v2/{ORG}/alerts/{existing_id}", payload, auth)
        print(f"  alert: PUT {spec['name']} (id={existing_id}) -> {code} {r}")
    else:
        print(f"  alert: CREATE {spec['name']} -> {code} {r}")


def main():
    auth = get_auth()
    pf = subprocess.Popen(
        ["kubectl", "--context", "mdapi-prod", "-n", "openobserve",
         "port-forward", "svc/openobserve", f"{OO_PORT}:5080"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        print("=== Pushover template ===")
        ensure_template(auth)
        print("=== Pushover destination ===")
        ensure_destination(auth)
        print("=== Alerts ===")
        for spec in ALERTS:
            ensure_alert(auth, spec)
    finally:
        pf.terminate()
        pf.wait(timeout=5)


if __name__ == "__main__":
    main()
