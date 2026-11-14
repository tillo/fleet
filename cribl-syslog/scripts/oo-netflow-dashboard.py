#!/usr/bin/env python3
"""Build or update the O2 dashboard 'Network flows — perimeter and inter-VLAN'.

Layer 0/1 of the in-cluster network visibility plan (infra-g1xc): READ the
IPFIX the router already exports. bpi-r4 ulogd2 (NFCT + IPFIX) -> svc/cribl-netflow
(.59:2055) -> O2 stream `netflow`, 7 d retention. The dashboard lives only in
OpenObserve, so this script is its source of truth:

    python3 cribl-syslog/scripts/oo-netflow-dashboard.py verify     # run every panel SQL, no writes
    python3 cribl-syslog/scripts/oo-netflow-dashboard.py create
    python3 cribl-syslog/scripts/oo-netflow-dashboard.py update <id>
    python3 cribl-syslog/scripts/oo-netflow-dashboard.py show <id>

Two rules every panel obeys (tests/test_netflow_dashboard_counts_true_flows.py):

  * COUNT TRUE FLOWS. Cribl samples NTP 1:100 and stamps every event with
    `sampled` (100 on a kept NTP flow, 1 on everything else), as a STRING. A
    true count is sum(cast(sampled as bigint)), true bytes
    sum(octettotalcount * cast(sampled as bigint)); count(*) undercounts NTP
    100x, and a bare sum(sampled) is a 422.
  * NTP AND DNS ARE EXCLUDED from every "who talks to whom" panel. The
    perimeter is a public service edge (the NTP pool, Technitium's public DNS
    view): together they are ~97 % of the true flows and would bury every
    other conversation. One panel shows the excluded classes, so the
    exclusion is visible rather than silent.

What the router cannot see, and this dashboard therefore cannot show:
traffic switched inside one VLAN (host to host on 192.168.1.0/24 never
reaches the router). That was Layer 2, deferred by the user on 2026-11-06.
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
DASHBOARD_ID = "7526388870758268928"
STREAM = "netflow"
S = f'"{STREAM}"'

# ── the two rules ────────────────────────────────────────────────────────────
FLOWS = "sum(cast(sampled as bigint))"
BYTES = "sum(octettotalcount * cast(sampled as bigint))"
NTP = "(destinationtransportport = 123 OR sourcetransportport = 123)"
DNS = ("(destinationtransportport IN (53, 853) OR sourcetransportport IN (53, 853))")
NOT_NTP_DNS = f"NOT {NTP} AND NOT {DNS}"


def _private(col):
    return (f"({col} LIKE '10.%' OR {col} LIKE '192.168.%' OR "
            f"{col} ~ '^172\\.(1[6-9]|2[0-9]|3[01])\\.')")


def _net24(col):
    return f"concat(split_part({col}, '.', 1), '.', split_part({col}, '.', 2), '.', split_part({col}, '.', 3), '.0/24')"


SRC, DST = "sourceipv4address", "destinationipv4address"
CLASS = (f"CASE WHEN {NTP} THEN 'ntp (sampled 1:100)' WHEN {DNS} THEN 'dns' "
         f"ELSE 'everything else' END")

BASE = dict(show_legends=True, legends_position=None, unit="numbers", decimals=0.0,
            top_results_others=False, axis_border_show=False, base_map=None,
            map_view=None, no_value_replacement="", wrap_table_cells=False,
            table_transpose=False, table_dynamic_columns=False)


def cfg(**kw):
    return {**BASE, **kw}


def q(sql, x=(), y=(), breakdown=()):
    return {"query": sql, "vrlFunctionQuery": None, "customQuery": True,
            "fields": {"stream": STREAM, "stream_type": "logs",
                       "x": list(x), "y": list(y), "z": [], "breakdown": list(breakdown),
                       "filter": {"filterType": "group", "logicalOperator": "AND",
                                  "conditions": []}},
            "config": {"promql_legend": "", "layer_type": "scatter",
                       "weight_fixed": 1.0, "limit": 0.0, "min": 0.0, "max": 100.0,
                       "time_shift": []}}


def col(label, alias, column, agg=None):
    d = {"label": label, "alias": alias, "column": column, "color": None}
    if agg:
        d["aggregationFunction"] = agg
    return d


def table(i, title, description, key_sql, key_label, where, layout, limit=25):
    """A top-N table: key, true flows, true bytes, NTP/DNS excluded."""
    sql = (f'SELECT {key_sql} AS "x_axis_1", {FLOWS} AS "y_axis_1", {BYTES} AS "y_axis_2" '
           f'FROM {S} WHERE {NOT_NTP_DNS} AND {where} '
           f'GROUP BY x_axis_1 ORDER BY y_axis_2 DESC LIMIT {limit}')
    return dict(id=f"p{i}", type="table", title=title, description=description,
                config=cfg(unit="numbers"),
                queries=[q(sql, x=[col(key_label, "x_axis_1", key_label)],
                           y=[col("Flows", "y_axis_1", "sampled", "sum"),
                              col("Bytes", "y_axis_2", "octettotalcount", "sum")])],
                layout=layout)


def lay(i, x, y, w, h):
    return dict(x=x, y=y, w=w, h=h, i=i)


PANELS = [
    dict(id="p1", type="metric", title="Flows, NTP and DNS excluded",
         description="True flow count (sampling undone) in the selected window.",
         config=cfg(show_legends=False),
         queries=[q(f'SELECT {FLOWS} AS "y_axis_1" FROM {S} WHERE {NOT_NTP_DNS}',
                    y=[col("Flows", "y_axis_1", "sampled", "sum")])],
         layout=lay(1, 0, 0, 16, 5)),
    dict(id="p2", type="metric", title="Bytes, NTP and DNS excluded",
         description="True byte count (sampling undone) in the selected window.",
         config=cfg(show_legends=False, unit="bytes"),
         queries=[q(f'SELECT {BYTES} AS "y_axis_1" FROM {S} WHERE {NOT_NTP_DNS}',
                    y=[col("Bytes", "y_axis_1", "octettotalcount", "sum")])],
         layout=lay(2, 16, 0, 16, 5)),
    dict(id="p3", type="metric", title="Share of flows that are NTP or DNS (%)",
         description="What the exclusion removes from every other panel.",
         config=cfg(show_legends=False, decimals=1.0),
         queries=[q(f'SELECT round(100.0 * sum(CASE WHEN {NTP} OR {DNS} THEN '
                    f'cast(sampled as bigint) ELSE 0 END) / {FLOWS}, 1) AS "y_axis_1" '
                    f'FROM {S}',
                    y=[col("Share", "y_axis_1", "sampled", "sum")])],
         layout=lay(3, 32, 0, 16, 5)),
    dict(id="p4", type="area-stacked", title="Flows by class (the excluded ones included)",
         description="NTP is the public pool (sampled 1:100, shown here as true flows); "
                     "DNS is Technitium's public view and the LAN resolvers.",
         config=cfg(),
         queries=[q(f'SELECT histogram(_timestamp) AS "x_axis_1", {CLASS} AS "breakdown_1", '
                    f'{FLOWS} AS "y_axis_1" FROM {S} GROUP BY x_axis_1, breakdown_1 '
                    f'ORDER BY x_axis_1 ASC',
                    x=[col("Time", "x_axis_1", "_timestamp", "histogram")],
                    y=[col("Flows", "y_axis_1", "sampled", "sum")],
                    breakdown=[col("Class", "breakdown_1", "destinationtransportport")])],
         layout=lay(4, 0, 5, 48, 8)),
    table(5, "Inter-VLAN: source /24 → destination /24",
          "Routed between internal networks, so the router sees it. Traffic inside "
          "one /24 never reaches the router and is not here.",
          f"concat({_net24(SRC)}, ' → ', {_net24(DST)})", "Networks",
          f"{_private(SRC)} AND {_private(DST)} AND {_net24(SRC)} <> {_net24(DST)}",
          lay(5, 0, 13, 24, 10)),
    table(6, "Top internal talkers to the Internet",
          "Internal source address → any public destination, by bytes.",
          SRC, "Source", f"{_private(SRC)} AND NOT {_private(DST)}",
          lay(6, 24, 13, 24, 10)),
    table(7, "Top Internet destinations",
          "Public destination address, by bytes, from any internal source.",
          DST, "Destination", f"{_private(SRC)} AND NOT {_private(DST)}",
          lay(7, 0, 23, 24, 10)),
    table(8, "Inbound to the public service edge, by port",
          "Public source → our addresses, by destination port (NTP/DNS excluded: "
          "they are the edge's own public services).",
          f"concat(cast(protocolidentifier as varchar), '/', "
          f"cast(destinationtransportport as varchar))", "Proto/port",
          f"NOT {_private(SRC)}", lay(8, 24, 23, 24, 10)),
    table(9, "Top destination ports, internal sources",
          "protocol/port of conversations started inside, NTP/DNS excluded.",
          f"concat(cast(protocolidentifier as varchar), '/', "
          f"cast(destinationtransportport as varchar))", "Proto/port",
          f"{_private(SRC)}", lay(9, 0, 33, 24, 10)),
    table(10, "Top internal pairs (source → destination)",
          "Who talks to whom, both ends internal and on different /24s.",
          f"concat({SRC}, ' → ', {DST})", "Pair",
          f"{_private(SRC)} AND {_private(DST)} AND {_net24(SRC)} <> {_net24(DST)}",
          lay(10, 24, 33, 24, 10)),
]

DASH = {
    "title": "Network flows — perimeter and inter-VLAN",
    "description": ("IPFIX from bpi-r4 (ulogd2) via Cribl into stream netflow. Counts are "
                    "TRUE flows and bytes: NTP is sampled 1:100 in Cribl and every panel "
                    "multiplies by `sampled`. NTP and DNS are excluded from the "
                    "who-talks-to-whom panels (the public NTP pool and DNS view are ~97 % "
                    "of all flows); the class panel shows them. Traffic inside one VLAN "
                    "never reaches the router and is not visible here. Source: fleet "
                    "cribl-syslog/scripts/oo-netflow-dashboard.py (infra-g1xc)."),
    "tabs": [{"tabId": "default", "name": "Overview", "panels": PANELS}],
    "version": 5,
}


# ── the O2 boundary ──────────────────────────────────────────────────────────

def _creds():
    # Root creds from the k8s secret; never printed, never written to disk.
    out = subprocess.run(
        ["kubectl", "--context", CTX, "-n", "openobserve", "get", "secret",
         "openobserve-admin", "-o", "json"],
        capture_output=True, text=True, check=True).stdout
    d = {k: base64.b64decode(v).decode() for k, v in json.loads(out)["data"].items()}
    user = d.get("ZO_ROOT_USER_EMAIL") or d.get("username")
    pw = d.get("ZO_ROOT_USER_PASSWORD") or d.get("password")
    return base64.b64encode(f"{user}:{pw}".encode()).decode()


def call(method, path, body=None):
    headers = {"Authorization": "Basic " + _creds(), "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(O2 + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def _with_query_type():
    for p in PANELS:
        p["queryType"] = "sql"
    return DASH


def verify():
    """Every panel's SQL over the last hour; exit 1 if any fails or returns nothing."""
    now = int(time.time() * 1_000_000)
    bad = 0
    for p in PANELS:
        st, res = call("POST", "/api/default/_search",
                       {"query": {"sql": p["queries"][0]["query"],
                                  "start_time": now - 3600 * 1_000_000,
                                  "end_time": now, "size": 5}})
        hits = res.get("hits") if isinstance(res, dict) else None
        ok = bool(hits)
        bad += not ok
        print(f"{p['id']:4} {p['type']:13} " +
              (f"rows={len(hits)} {str(hits)[:100]}" if ok else f"FAIL {str(res)[:200]}"))
    return 1 if bad else 0


def create():
    st, res = call("POST", "/api/default/dashboards", _with_query_type())
    print("created", res["v5"]["dashboardId"] if st == 200 else f"failed {st} {res}")
    return 0 if st == 200 else 1


def update(did):
    st, cur = call("GET", f"/api/default/dashboards/{did}")
    if st != 200:
        print("cannot read current dashboard:", st, cur)
        return 1
    # The optimistic-concurrency hash goes in the QUERY STRING; in the body O2
    # ignores it and answers 500 "missing or invalid hash value".
    st, res = call("PUT", f"/api/default/dashboards/{did}?hash={cur['hash']}", _with_query_type())
    print("update:", st, str(res)[:200])
    return 0 if st == 200 else 1


def show(did):
    st, res = call("GET", f"/api/default/dashboards/{did}")
    v = res.get("v5") or {}
    print("GET:", st, "|", v.get("title"), "| panels", len(v["tabs"][0]["panels"]))
    return 0 if st == 200 else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = {"create": lambda: create(),
           "update": lambda: update(a[1] if len(a) > 1 else DASHBOARD_ID),
           "show": lambda: show(a[1] if len(a) > 1 else DASHBOARD_ID)}.get(a[0] if a else "", verify)
    sys.exit(cmd())
