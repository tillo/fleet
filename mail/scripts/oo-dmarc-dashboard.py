#!/usr/bin/env python3
"""Build or update the O2 dashboard 'DMARC — aggregate reports'.

The dashboard lives only in OpenObserve (dashboards are not in the fleet bundle),
so this script is its source of truth. Run it to recreate the dashboard from
scratch, or to push a query change.

    python3 mail/scripts/oo-dmarc-dashboard.py verify     # run every panel SQL, no writes
    python3 mail/scripts/oo-dmarc-dashboard.py create     # create a new dashboard
    python3 mail/scripts/oo-dmarc-dashboard.py update <id>
    python3 mail/scripts/oo-dmarc-dashboard.py show <id>

Data source: Windmill f/probe/dmarc_reports_to_o2 → O2 stream `dmarc_reports_v2`.
Background and traps: memory dmarc_aggregate_pipeline_mailbox_to_o2_2026_10_23.
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
DASHBOARD_ID = "7519390488034541568"

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
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


# ── the domain filter ────────────────────────────────────────────────────────
# report_domain is NOT unique to us: dmarc@tillo.ch still receives reports for
# domains we no longer host (back to 2019, roughly half the stream). Every panel
# filters to the hosted set, which also keeps other people's domains off the page.
DOMAINS = ("'tillo.ch','mdapi.ch','mail.mdapi.ch','coders.ch','coiffuredreams.ch',"
           "'dellambrogio.ch','dellambrogio.com','dellambrogio.net','dellambrogio.org',"
           "'ivodellambrogio.ch','envuassu.ch','mdapi.org','lithia.eu'")

# Scope on the REPORT period, never on _timestamp: O2 refuses documents older than
# 5 hours, so the pipeline stamps every row with its ingest time and the dashboard
# time picker cannot tell 2019 from today.
RECENT = "period_begin > to_unixtime(now()) - 7776000"  # 90 days
W = f"report_domain IN ({DOMAINS}) AND {RECENT}"
S = '"dmarc_reports_v2"'   # v1 is the superseded generation, every doc duplicated

BASE = dict(show_legends=True, legends_position=None, unit="numbers", decimals=0.0,
            top_results_others=False, axis_border_show=False, base_map=None,
            map_view=None, no_value_replacement="", wrap_table_cells=False,
            table_transpose=False, table_dynamic_columns=False)


def cfg(**kw):
    c = dict(BASE)
    c.update(kw)
    return c


def q(sql, x=None, y=None, breakdown=None):
    return {"query": sql, "vrlFunctionQuery": None, "customQuery": True,
            "fields": {"stream": "dmarc_reports_v2", "stream_type": "logs",
                       "x": x or [], "y": y or [], "z": [],
                       "breakdown": breakdown or [],
                       "filter": {"filterType": "group", "logicalOperator": "AND",
                                  "conditions": []}},
            "config": {"promql_legend": "", "layer_type": "scatter",
                       "weight_fixed": 1.0, "limit": 0.0, "min": 0.0, "max": 100.0,
                       "time_shift": []}}


def xcol(label, alias, column, agg=None, sort=None):
    d = {"label": label, "alias": alias, "column": column, "color": None}
    if agg:
        d["aggregationFunction"] = agg
    if sort:
        d["sortBy"] = sort
    return d


def ycol(label, alias, column, agg="count"):
    return {"label": label, "alias": alias, "column": column, "color": None,
            "aggregationFunction": agg}


# NOTE align_ok is Int64 0/1, not a boolean — `NOT align_ok` raises
# "Unary operator 'NOT' requires a boolean expression, got Int64".
PANELS = [
    dict(id="p1", type="metric", title="Messages covered, last 90 days",
         description="", config=cfg(show_legends=False),
         queries=[q(f'SELECT sum(count) AS "y_axis_1" FROM {S} WHERE {W}',
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=0, y=0, w=12, h=5, i=1)),
    dict(id="p2", type="metric", title="Records ingested, last 90 days",
         description="", config=cfg(show_legends=False),
         queries=[q(f'SELECT count(*) AS "y_axis_1" FROM {S} WHERE {W}',
                    y=[ycol("Records", "y_axis_1", "count", "count")])],
         layout=dict(x=12, y=0, w=12, h=5, i=2)),
    dict(id="p3", type="metric", title="Aligned messages (%), last 90 days",
         description="", config=cfg(show_legends=False, decimals=2.0),
         queries=[q(f'SELECT round(100.0 * sum(CASE WHEN align_ok = 1 THEN count ELSE 0 END)'
                    f' / sum(count), 2) AS "y_axis_1" FROM {S} WHERE {W}',
                    y=[ycol("Aligned", "y_axis_1", "align_ok", "count")])],
         layout=dict(x=24, y=0, w=12, h=5, i=3)),
    dict(id="p4", type="metric", title="Actioned messages, last 90 days",
         description="Includes the kernel-list copies (legitimate, see the kernel "
                     "panel) — they are most of this number.",
         config=cfg(show_legends=False),
         queries=[q(f"SELECT sum(count) AS \"y_axis_1\" FROM {S} WHERE {W} AND "
                    f"disposition IN ('quarantine','reject')",
                    y=[ycol("Actioned", "y_axis_1", "count", "sum")])],
         layout=dict(x=36, y=0, w=12, h=5, i=4)),
    dict(id="p5", type="area-stacked", title="Messages by disposition, arrival time",
         description="", config=cfg(),
         queries=[q(f'SELECT histogram(_timestamp) AS "x_axis_1", disposition AS '
                    f'"breakdown_1", sum(count) AS "y_axis_1" FROM {S} WHERE {W} '
                    f'GROUP BY x_axis_1, breakdown_1 ORDER BY x_axis_1 ASC',
                    x=[xcol("Time", "x_axis_1", "_timestamp", "histogram", "ASC")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")],
                    breakdown=[{"label": "Disposition", "alias": "breakdown_1",
                                "column": "disposition", "color": None}])],
         layout=dict(x=0, y=5, w=48, h=8, i=5)),
    dict(id="p6", type="pie", title="Messages by reporter, last 90 days",
         description="", config=cfg(),
         queries=[q(f'SELECT org_name AS "x_axis_1", sum(count) AS "y_axis_1" '
                    f'FROM {S} WHERE {W} GROUP BY x_axis_1 ORDER BY y_axis_1 DESC',
                    x=[xcol("Reporter", "x_axis_1", "org_name")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=0, y=13, w=24, h=8, i=6)),
    dict(id="p7", type="pie", title="Auth outcome, last 90 days",
         description="", config=cfg(),
         queries=[q(f"SELECT CASE WHEN align_ok = 1 THEN 'aligned' ELSE 'not aligned' END "
                    f'AS "x_axis_1", sum(count) AS "y_axis_1" FROM {S} WHERE {W} '
                    f'GROUP BY x_axis_1 ORDER BY y_axis_1 DESC',
                    x=[xcol("Outcome", "x_axis_1", "align_ok")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=24, y=13, w=24, h=8, i=7)),
    dict(id="p8", type="table", title="Top source IPs, last 90 days",
         description="Volume view, so the kernel lists' forwarders (Linode/Akamai "
                     "ranges) dominate it — that traffic is the user's own list "
                     "posts, not abuse.",
         config=cfg(),
         queries=[q(f'SELECT source_ip AS "x_axis_1", sum(count) AS "y_axis_1" FROM {S} '
                    f'WHERE {W} GROUP BY x_axis_1 ORDER BY y_axis_1 DESC LIMIT 20',
                    x=[xcol("Source IP", "x_axis_1", "source_ip")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=0, y=21, w=24, h=9, i=8)),
    dict(id="p9", type="table", title="Forged or failing senders, last 90 days",
         description="Kernel mailing-list forwarding is excluded — see the panel below.",
         config=cfg(),
         queries=[q(f'SELECT concat(header_from, \' ← \', source_ip, \' for \', '
                    f'report_domain) AS "x_axis_1", sum(count) AS "y_axis_1" FROM {S} '
                    f"WHERE {W} AND align_ok = 0 AND disposition IN "
                    f"('quarantine','reject') AND spf_domain NOT LIKE '%kernel.org' "
                    f'GROUP BY x_axis_1 ORDER BY y_axis_1 DESC LIMIT 20',
                    x=[xcol("Forged sender", "x_axis_1", "header_from")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=24, y=21, w=24, h=9, i=9)),
    dict(id="p10", type="table", title="Messages per domain, last 90 days",
         description="", config=cfg(),
         queries=[q(f'SELECT report_domain AS "x_axis_1", sum(count) AS "y_axis_1" FROM {S} '
                    f'WHERE {W} GROUP BY x_axis_1 ORDER BY y_axis_1 DESC',
                    x=[xcol("Domain", "x_axis_1", "report_domain")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=0, y=30, w=24, h=9, i=10)),
    dict(id="p11", type="table", title="Our own IPs seen as sources, last 90 days",
         description="", config=cfg(),
         queries=[q(f'SELECT source_ip AS "x_axis_1", sum(count) AS "y_axis_1" FROM {S} '
                    f"WHERE {W} AND source_ip LIKE '31.3.128.%' "
                    f'GROUP BY x_axis_1 ORDER BY y_axis_1 DESC LIMIT 20',
                    x=[xcol("Source IP", "x_axis_1", "source_ip")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=24, y=30, w=24, h=9, i=11)),
    dict(id="p12", type="table",
         title="Kernel list forwarding — expected failures, last 90 days",
         description="tillo@tillo.ch posts to the kernel lists; vger/lore redistribute "
                     "with a broken DKIM body hash (d=tillo.ch permerror) and no SPF for "
                     "the *.lore.kernel.org senders, so DMARC fails and receivers "
                     "quarantine the list's copies. Legitimate traffic, not spoofing.",
         config=cfg(),
         queries=[q(f'SELECT concat(spf_domain, \' ← \', source_ip) AS "x_axis_1", '
                    f'sum(count) AS "y_axis_1" FROM {S} '
                    f"WHERE {W} AND align_ok = 0 AND spf_domain LIKE '%kernel.org' "
                    f'GROUP BY x_axis_1 ORDER BY y_axis_1 DESC LIMIT 20',
                    x=[xcol("List system", "x_axis_1", "spf_domain")],
                    y=[ycol("Messages", "y_axis_1", "count", "sum")])],
         layout=dict(x=0, y=39, w=24, h=9, i=12)),
]

DASH = {
    "title": "DMARC — aggregate reports",
    "description": ("DMARC aggregate (rua) reports for the 13 hosted domains, ingested "
                    "every 15 minutes by Windmill f/probe/dmarc_reports_to_o2 into "
                    "stream dmarc_reports_v2. Every panel is scoped by the REPORT period "
                    "(period_begin, last 90 days), not by arrival: a document's "
                    "_timestamp is its ingest time, because O2 refuses documents older "
                    "than 5 hours and the report period would otherwise be rejected. "
                    "The mailbox also holds reports back to 2019 and for domains we no "
                    "longer host; those are deliberately excluded here. Kernel "
                    "mailing-list forwarding (vger/lore) is reported in its own panel "
                    "and kept out of the 'forged or failing senders' table: the list "
                    "breaks our DKIM, so its copies fail DMARC without anyone attacking."),
    "tabs": [{"tabId": "default", "name": "Overview", "panels": PANELS}],
    "version": 5,
}


def verify():
    now = int(time.time() * 1_000_000)
    for p in PANELS:
        st, res = call("POST", "/api/default/_search",
                       {"query": {"sql": p["queries"][0]["query"],
                                  "start_time": now - 30 * 86400 * 1_000_000,
                                  "end_time": now, "size": 5}})
        hits = res.get("hits") if isinstance(res, dict) else None
        ok = isinstance(hits, list)
        print(f"{p['id']:4} {p['type']:13} " +
              (f"rows={len(hits)} {str(hits)[:90]}" if ok else f"ERROR {str(res)[:160]}"))


def create():
    for p in DASH["tabs"][0]["panels"]:
        p["queryType"] = "sql"
    st, res = call("POST", "/api/default/dashboards", DASH)
    if st == 200:
        print("created", res["v5"]["dashboardId"])
    else:
        print("failed", st, res)


def update(did):
    st, cur = call("GET", f"/api/default/dashboards/{did}")
    if st != 200:
        print("cannot read current dashboard:", st, cur)
        return
    for p in DASH["tabs"][0]["panels"]:
        p["queryType"] = "sql"
    # The optimistic-concurrency hash goes in the QUERY STRING. In the body it
    # is ignored and the PUT fails with "missing or invalid hash value. BUG"
    # (O2 500s even when you round-trip its own GET response unchanged).
    st, res = call("PUT", f"/api/default/dashboards/{did}?hash={cur['hash']}", dict(DASH))
    print("update:", st, str(res)[:200])


def show(did):
    st, res = call("GET", f"/api/default/dashboards/{did}")
    v = res.get("v5") or {}
    print("GET:", st, "|", v.get("title"), "| panels", len(v["tabs"][0]["panels"]))
    for p in v["tabs"][0]["panels"]:
        print("  ", p["id"], p["type"], "|", p["title"])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "create":
        create()
    elif a and a[0] == "update":
        update(a[1] if len(a) > 1 else DASHBOARD_ID)
    elif a and a[0] == "show":
        show(a[1] if len(a) > 1 else DASHBOARD_ID)
    else:
        verify()
