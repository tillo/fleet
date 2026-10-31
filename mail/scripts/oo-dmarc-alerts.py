#!/usr/bin/env python3
"""Build or update the O2 alerts on the DMARC aggregate-report stream (bead infra-l0j3).

The alerts live only in OpenObserve, like the dashboard built by
oo-dmarc-dashboard.py, so this script is their source of truth. Not a Fleet
resource: run it by hand from the jump seat after a change.

    python3 mail/scripts/oo-dmarc-alerts.py verify     # run each alert SQL on its live window, no writes
    python3 mail/scripts/oo-dmarc-alerts.py backtest   # replay both alerts over 365 d by report period
    python3 mail/scripts/oo-dmarc-alerts.py apply      # create or update (by name) + read back
    python3 mail/scripts/oo-dmarc-alerts.py show       # print the live objects' key fields

Data source: Windmill f/probe/dmarc_reports_to_o2 → O2 stream `dmarc_reports_v2`.
Background and traps: memory dmarc_aggregate_pipeline_mailbox_to_o2_2026_10_23,
o2_native_alert_crud_api_and_sql_gotchas_2026_10_06.

How the time window works here
------------------------------
`_timestamp` is the INGEST time (O2 refuses documents older than 5 h), so the
alert's `period` window selects reports that ARRIVED in it, which is exactly
"new evidence since the last check". `period_begin` then scopes on the report's
own period: a 7-day guard, so a stream re-backfill or a reporter's very late
report never pages on old history.

Both windows are aligned (align_time) and the spoof alert uses period ==
frequency == 60 min, so every row is evaluated in exactly one window: no double
fire, and no silence needed to dedup. Hourly rather than the estate's usual
15 min because the job stamps `_timestamp` at the START of its :00/:15/:30/:45
run: a 60-min window keeps the nearest run 15 min away from every window edge,
and an hour of latency is nothing on a daily aggregate.

The list-forwarding exclusion (both alerts)
-------------------------------------------
tillo@tillo.ch posts to the kernel lists. vger/lore redistribute with a broken
d=tillo.ch DKIM body hash and *.lore.kernel.org has no SPF, so those copies fail
DMARC and receivers quarantine them: the user's own traffic, not spoofing. The
lists hosted on lists.infradead.org (same kernel community) do the same: Outlook
quarantined 17 of their tillo.ch copies on 2026-05-28 and 2026-07-05. Both are
excluded on spf_domain, as dashboard panel p9 does for kernel.org. Caveat:
spf_domain is the sender's own envelope domain, so a forger could claim one to
hide. Accepted: the alternative fires on every list post.
"""
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

CTX = "mdapi-prod-direct"
O2 = "http://openobserve.openobserve.svc.cluster.local:5080"
ORG = "default"
STREAM = "dmarc_reports_v2"
DESTINATION = "pushover_mdapi"   # owned by Windmill f/heal/openobserve_pushover_sync; never written here

# ── O2 credentials ───────────────────────────────────────────────────────────
# Root creds straight from the k8s secret; never printed, never written to disk.


def _creds():
    out = subprocess.run(
        ["kubectl", "--context", CTX, "-n", "openobserve", "get", "secret",
         "openobserve-admin", "-o", "json"],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)["data"]
    d = {k: base64.b64decode(v).decode() for k, v in data.items()}
    user = d.get("ZO_ROOT_USER_EMAIL") or d.get("username")
    pw = d.get("ZO_ROOT_USER_PASSWORD") or d.get("password")
    return base64.b64encode(f"{user}:{pw}".encode()).decode()


H = {"Authorization": "Basic " + _creds(), "Content-Type": "application/json"}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(O2 + path, method=method, data=data, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def search(sql, seconds):
    now = int(time.time() * 1_000_000)
    st, res = call("POST", f"/api/{ORG}/_search",
                   {"query": {"sql": sql, "start_time": now - seconds * 1_000_000,
                              "end_time": now, "size": 500}})
    if st != 200 or not isinstance(res, dict):
        raise SystemExit(f"search failed {st}: {res}")
    return res["hits"]


# ── the filters ──────────────────────────────────────────────────────────────
# Keep DOMAINS in step with oo-dmarc-dashboard.py. report_domain is NOT
# unique to us: the mailbox still receives reports for domains we no longer host
# (back to 2019), and those must stay out of every query.
DOMAINS = ("'tillo.ch','mdapi.ch','mail.mdapi.ch','coders.ch','coiffuredreams.ch',"
           "'dellambrogio.ch','dellambrogio.com','dellambrogio.net','dellambrogio.org',"
           "'ivodellambrogio.ch','envuassu.ch','mdapi.org','lithia.eu'")
OURS = f"report_domain IN ({DOMAINS})"
FOREIGN = "source_ip NOT LIKE '31.3.128.%'"          # our own outbound /24
# coalesce: 13 rows carry an empty spf_domain, and a NULL would make NOT LIKE
# drop the row silently instead of alerting on it.
NOT_LISTS = ("coalesce(spf_domain, '') NOT LIKE '%kernel.org' "
             "AND coalesce(spf_domain, '') NOT LIKE '%infradead.org'")
RECENT = "period_begin > to_unixtime(now()) - 604800"  # report period within 7 d
S = f'"{STREAM}"'

SPOOF_COLS = "header_from, source_ip, spf_domain, spf_result, dkim_result, disposition, org_name"
SPOOF_WHERE = (f"{OURS} AND disposition IN ('quarantine','reject') AND {FOREIGN} "
               f"AND {NOT_LISTS}")
VOLUME_WHERE = f"{OURS} AND align_ok = 0 AND {FOREIGN} AND {NOT_LISTS}"
VOLUME_MIN = 500

# NOTE align_ok is Int64 0/1, not a boolean: `NOT align_ok` raises
# "Unary operator 'NOT' requires a boolean expression, got Int64".
ALERTS = [
    {
        "name": "dmarc_own_domain_spoof_actioned",
        "description": (
            "A receiver QUARANTINED or REJECTED mail claiming one of our 13 hosted "
            "domains (header From) that came from a source OUTSIDE our own outbound "
            "31.3.128.0/24, i.e. someone else sending as us: real spoofing, like "
            "autodetails.mdapi.org from Azure 52.102.171.42/52.102.194.42 on "
            "2026-10-08, stopped then only because sp=reject. Kernel and infradead "
            "list forwarding is EXCLUDED on spf_domain: the lists break our DKIM and "
            "receivers quarantine the user's own list posts. One row per sender "
            "tuple from reports that ARRIVED in the last hour (_timestamp is ingest "
            "time) whose report period began in the last 7 d. BASELINE 365 d by "
            "report period: 11 rows on 10 days (under one a month), all of them "
            "forged-sender events (dellambrogio.ch x6, coders.ch x2 via IONOS, "
            "mirror.tillo.ch, the two Azure autodetails.mdapi.org). Without the "
            "infradead exclusion 2 more days (2026-05-28, 07-05) fire on list "
            "copies. Source: Windmill "
            "f/probe/dmarc_reports_to_o2; built by fleet mail/scripts/oo-dmarc-alerts.py; bead "
            "infra-l0j3. Revert: DELETE /api/v2/default/alerts/<id>."
        ),
        "trigger_condition": {
            "period": 60, "operator": ">=", "threshold": 1, "frequency": 60,
            "frequency_type": "minutes", "cron": "", "silence": 0,
            "tolerance_in_secs": 0, "align_time": True,
        },
        "query_condition": {
            "type": "sql",
            "sql": (f"SELECT {SPOOF_COLS}, min(period_begin_iso) AS period_from, "
                    f"sum(count) AS msgs FROM {S} WHERE {SPOOF_WHERE} AND {RECENT} "
                    f"GROUP BY {SPOOF_COLS}"),
        },
        "row_template": (
            "DMARC spoof: {msgs} msg(s) From {header_from} sent by {source_ip} "
            "(envelope {spf_domain}, spf={spf_result}, dkim={dkim_result}) were "
            "{disposition}ed by {org_name}, report period from {period_from}. Not our "
            "infrastructure. Dashboard 'DMARC — aggregate reports', panel 'Forged or "
            "failing senders'."
        ),
        "backtest_sql": (f"SELECT substr(period_begin_iso, 1, 10) AS day, {SPOOF_COLS}, "
                         f"sum(count) AS msgs FROM {S} WHERE {SPOOF_WHERE} AND "
                         f"period_begin > to_unixtime(now()) - 31536000 "
                         f"GROUP BY day, {SPOOF_COLS} ORDER BY day"),
    },
    {
        "name": "dmarc_unaligned_volume_spike",
        "description": (
            f"Volume spike: >= {VOLUME_MIN} messages claiming our hosted domains "
            "FAILED DMARC alignment from sources outside our 31.3.128.0/24, in reports "
            "that arrived in the last 24 h (report period within 7 d), whatever the "
            "receiver then did with them. That is the scale of a forged-sender campaign "
            "(the 2026-10-23 Kievline backscatter review, item 5); single forged "
            "messages are dmarc_own_domain_spoof_actioned's job. Total volume is NOT "
            "alerted: 99 % of it is kernel/infradead list forwarding (49,483 msgs on "
            "2026-05-28, 49,290 of them list copies) and would fire on every list "
            "post, so the list exclusion applies here too. BASELINE 365 d by report "
            "day: max 101 unaligned non-list foreign msgs/day (2026-05-28, 24 IPs, "
            "list subscribers' own forwarders), next 70; the 500 bar keeps a 5x margin "
            "and still a 2.5x one when a 24 h arrival window straddles two report "
            "days. Evaluated hourly over a rolling 24 h window, silenced 24 h after a "
            "fire. Built by fleet mail/scripts/oo-dmarc-alerts.py; bead infra-l0j3. Revert: DELETE "
            "/api/v2/default/alerts/<id>."
        ),
        "trigger_condition": {
            "period": 1440, "operator": ">=", "threshold": 1, "frequency": 60,
            "frequency_type": "minutes", "cron": "", "silence": 1440,
            "tolerance_in_secs": 0, "align_time": True,
        },
        "query_condition": {
            "type": "sql",
            "sql": (f"SELECT sum(count) AS msgs, count(DISTINCT source_ip) AS ips, "
                    f"string_agg(DISTINCT header_from, ',') AS domains FROM {S} "
                    f"WHERE {VOLUME_WHERE} AND {RECENT} "
                    f"HAVING sum(count) >= {VOLUME_MIN}"),
        },
        "row_template": (
            "DMARC volume spike: {msgs} messages From {domains} failed DMARC alignment "
            "from {ips} source IPs outside our infrastructure, in reports received in "
            f"the last 24 h (365-d max: 101/day, alert bar {VOLUME_MIN}). Likely a "
            "forged-sender campaign; check the dashboard 'DMARC — aggregate "
            "reports', panel 'Forged or failing senders'."
        ),
        "backtest_sql": (f"SELECT substr(period_begin_iso, 1, 10) AS day, "
                         f"sum(count) AS msgs, count(DISTINCT source_ip) AS ips FROM {S} "
                         f"WHERE {VOLUME_WHERE} AND "
                         f"period_begin > to_unixtime(now()) - 31536000 "
                         f"GROUP BY day ORDER BY msgs DESC LIMIT 10"),
    },
]


def payload(spec):
    return {
        "name": spec["name"], "description": spec["description"],
        "org_id": ORG, "stream_name": STREAM, "stream_type": "logs",
        "is_real_time": False, "enabled": True, "destinations": [DESTINATION],
        "trigger_condition": spec["trigger_condition"],
        "query_condition": spec["query_condition"],
        "row_template": spec["row_template"], "row_template_type": "String",
        "context_attributes": {}, "tz_offset": 0, "creates_incident": False,
        "workflows": [], "pending_period_sec": 0,
    }


def existing_ids():
    st, res = call("GET", f"/api/v2/{ORG}/alerts")
    if st != 200:
        raise SystemExit(f"alert list failed {st}: {res}")
    ids = {}
    for a in res.get("list") or []:
        ids.setdefault(a["name"], []).append(a.get("alert_id") or a.get("id"))
    return ids


def verify():
    for spec in ALERTS:
        hits = search(spec["query_condition"]["sql"],
                      spec["trigger_condition"]["period"] * 60)
        print(f"{spec['name']}: {len(hits)} row(s) in the live window "
              f"({'WOULD FIRE' if hits else 'quiet'})")
        for h in hits:
            print("  ", json.dumps(h))


def backtest():
    for spec in ALERTS:
        hits = search(spec["backtest_sql"], 400 * 86400)
        print(f"== {spec['name']}: {len(hits)} row(s), 365 d by report period")
        for h in hits:
            print("  ", json.dumps(h))


def apply():
    ids = existing_ids()
    for spec in ALERTS:
        found = ids.get(spec["name"], [])
        if len(found) > 1:
            raise SystemExit(f"{spec['name']} exists {len(found)} times: {found}. "
                             "Delete the duplicates first.")
        # POST is NOT an upsert (a second POST makes a same-named duplicate), so
        # PUT by id when the name exists. A PUT re-evaluates at once: an
        # already-true condition pages straight away.
        if found:
            st, res = call("PUT", f"/api/v2/{ORG}/alerts/{found[0]}", payload(spec))
            aid = found[0]
        else:
            st, res = call("POST", f"/api/v2/{ORG}/alerts", payload(spec))
            aid = (res or {}).get("id") if isinstance(res, dict) else None
        print(f"{spec['name']}: {'PUT' if found else 'POST'} {st} {str(res)[:120]}")
        if st != 200 or not aid:
            raise SystemExit("write failed")
        readback(aid, spec)


def readback(aid, spec):
    st, d = call("GET", f"/api/v2/{ORG}/alerts/{aid}")
    if st != 200:
        raise SystemExit(f"readback failed {st}: {d}")
    want = payload(spec)
    checks = {
        "stream_name": (d.get("stream_name"), want["stream_name"]),
        "destinations": (d.get("destinations"), want["destinations"]),
        "enabled": (d.get("enabled"), True),
        "sql": (d["query_condition"]["sql"], want["query_condition"]["sql"]),
        "row_template": (d.get("row_template"), want["row_template"]),
        **{f"trigger.{k}": (d["trigger_condition"].get(k), v)
           for k, v in want["trigger_condition"].items() if k != "cron"},
    }
    bad = [k for k, (got, exp) in checks.items() if got != exp]
    for k in bad:
        print(f"   BAD {k}: got {checks[k][0]!r}, want {checks[k][1]!r}")
    if bad:
        raise SystemExit(f"readback mismatch on {spec['name']}")
    print(f"   readback OK (id {aid})")


def show():
    ids = existing_ids()
    for spec in ALERTS:
        for aid in ids.get(spec["name"], []):
            st, d = call("GET", f"/api/v2/{ORG}/alerts/{aid}")
            tc = d["trigger_condition"]
            print(f"{spec['name']} id={aid} enabled={d['enabled']} "
                  f"period={tc['period']} freq={tc['frequency']} silence={tc['silence']} "
                  f"dest={d['destinations']}")
        if spec["name"] not in ids:
            print(f"{spec['name']}: ABSENT")


if __name__ == "__main__":
    a = sys.argv[1:]
    {"backtest": backtest, "apply": apply, "show": show}.get(a[0] if a else "", verify)()
