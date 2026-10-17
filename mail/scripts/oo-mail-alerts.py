#!/usr/bin/env python3
"""
Set up OpenObserve scheduled alerts on the mail_events stream (mail stack).

  Alert 1 - mail_delivery_failed:
    Permanent delivery failures only: postfix status=bounced (a DSN was
    generated: 5xx from the next hop, or a local recipient unknown / over
    quota) or status=expired (max queue lifetime reached). Terminal states --
    the message will not retry, and it leaves the queue immediately, so
    postfix_showq / MailQueueBacklog / MailQueueStuck can never see it.

  Alert 2 - mail_events_pipeline_coverage:
    The guard for Alert 1. mail_delivery_failed can only ever read what the
    Cribl route `mail_events_to_oo` and the JS pipeline `mail_events` put in
    the stream: if either stops, the count is 0 and the bounce alert is
    silently blind -- a green alert meaning "nothing to report" and "nothing
    arriving" are indistinguishable. This alert fails CLOSED on both halves:
      * volume  -- an hour with < 1000 rows (normal 2283-6290 over 7 d);
      * parse   -- an hour where < 99% of rows carry `program`, the field the
                   pipeline's own regex derives (100% in every hour measured).
    Modelled on pod_logs_service_name_coverage, which guards the O2
    correlation join key the same way.

  Why there is no "deferred" companion alert here
  -----------------------------------------------
  Deferrals are already covered, and better, by the metric side:
  `app-mail.queue` in monitoring-rules/01-mail.yml raises MailQueueBacklog
  (>=5 messages deferred past one retry cycle, warning) and MailQueueStuck
  (>=5 deferred ~3h, critical) off postfix_showq_message_age_seconds, with
  MailQueueMetricsAbsent guarding the exporter itself. Queue AGE is the right
  signal for a deferral (the whole point is "still sitting there"), and the
  rule's own comment settles the threshold question: "1-2 long-retry
  deferrals to dead EXTERNAL destinations are normal background".
  Measured 2026-10-06 over 7 d of pod_logs: 2 deferrals total. A log-derived
  deferral count would fire on that normal background and would violate
  alert hygiene, which drops duplicates of vmalert coverage. Don't add one.

  A bounce/expire, by contrast, is invisible to every metric alert we have.

Both alerts fire to the existing Pushover account via the SAME destination and
template (pushover_mdapi) the rest of the estate uses.

NOTE: this script is idempotent per alert NAME -- adding an entry to ALERTS and
re-running creates it without touching the ones already there.

Idempotent: PUTs the alert by id when the name already exists, POSTs only when
absent -- re-running never duplicates an alert.

NOTE: this script deliberately does NOT create or update the pushover_mdapi
template or destination. Those are owned by Windmill f/heal/openobserve_pushover_sync,
which reconciles token/user from Akeyless (/mdapi/pushover/o2-api-token,
/mdapi/pushover/user-key) on a schedule. The older
technitium/scripts/oo-technitium-alerts.py DOES rewrite the shared template from
/mdapi/pushover/mdapi-alertmanager-token -- a different app token -- so
re-running that script token-flips the estate-wide template until the heal job
puts it back. Don't copy that part.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The jump seat runs INSIDE the cluster, so the Service DNS name is reachable
# directly and needs no port-forward. A port-forward is kept only as a fallback
# for running this from off-cluster. Override with OO_URL if you need to.
OO_URL = os.environ.get(
    "OO_URL", "http://openobserve.openobserve.svc.cluster.local:5080")
OO_PORT = 15080
ORG = "default"

GATEWAY_URL = os.environ.get(
    "AKEYLESS_GATEWAY_URL", "https://cm.mdapi.ch/akeyless-api/")

DESTINATION_NAME = "pushover_mdapi"

# Measured 2026-10-06 before enabling, replayed out of pod_logs (mail_events
# only started at 2026-10-05T21:11Z; the same postfix lines lived in pod_logs
# before the Cribl carve-out):
#   status=sent      743 / 7 d   (142 nonzero hourly buckets, max 24/h)
#   status=deferred    2 / 7 d   (one bucket, max 2)
#   status=bounced     0 / 7 d
#   status=expired     0 / 7 d
#   status=rejected   26 / 12 h  -- inbound spam hygiene, NOT alerted
# Control proving the query shape works on this stream: the rejected count
# above was produced by the identical SELECT ... HAVING shape.
#
# For mail_events_pipeline_coverage, the same 7 d replayed out of pod_logs:
#   rows/hour  min 2283, max 6290  (159 hourly buckets present)
#   hours that would fire on count(*) < 1000: exactly TWO --
#     2026-10-03T08:00 (505 rows, the recovery hour after the triple-host-down
#       incident, whose 06:00 and 07:00 buckets are absent entirely -> total 0)
#     2026-10-05T21:00 (538 rows, the CRIBL CARVE-OUT BOUNDARY: mail lines
#       stopped landing in pod_logs when the route went live at 21:11. An
#       artifact of the move, not an incident -- it cannot recur on
#       mail_events, whose first bucket is that same 21:11.)
#   margin: 2283 / 1000 = 2.3x below the quietest normal hour.
#   parse half: count(program) < count(*) * 0.99 -- 0 hours fire across the
#     10 h of live mail_events (program is 100% populated in every bucket).
#     CAVEAT: only 10 h of evidence for the ratio, because pre-carve-out
#     pod_logs rows are raw syslog and have no `program` field at all.
ALERTS = [
    {
        "name": "mail_delivery_failed",
        "description": (
            "Log-derived mail delivery FAILURES. Cribl pipeline mail_events "
            "(route mail_events_to_oo, index mail_events, 90d retention) parses "
            "postfix lines from ns mail (docker-mailserver). status=bounced (a DSN "
            "was generated: 5xx from the next hop, or a local recipient "
            "unknown/over quota) and status=expired (max queue lifetime reached, "
            "DSN generated) are TERMINAL: the message will not retry, and it "
            "leaves the queue immediately, so postfix_showq / MailQueueBacklog / "
            "MailQueueStuck can NEVER see it - this is the log-side counterpart to "
            "those metric-side queue alerts, not a duplicate. BASELINE before "
            "enabling (2026-10-06): 0 bounced + 0 expired over the 7 days replayed "
            "from pod_logs (same window holds 743 status=sent), so threshold 1 "
            "does not fire on normal traffic. CONTROL in the same window, identical "
            "query shape: status='rejected' returns 26 rows / 12h - that is normal "
            "inbound spam hygiene (bad-HELO and unknown-recipient RCPT rejects) and "
            "is DELIBERATELY not alerted; a reject SURGE would be a separate "
            "signal. Trace: O2 -> stream mail_events -> status IN "
            "('bounced','expired'); the sender got a DSN, so this is not silent "
            "loss. Revert: DELETE /api/v2/default/alerts/<id>."
        ),
        "trigger_condition": {
            "period": 15,
            "operator": ">=",
            "threshold": 1,
            "frequency": 15,
            "frequency_type": "minutes",
            "cron": "",
            "silence": 60,
            "tolerance_in_secs": 0,
            "align_time": True,
        },
        "query_condition": {
            "type": "sql",
            "sql": (
                'SELECT count(*) AS c FROM "mail_events" '
                "WHERE status IN ('bounced','expired') "
                "HAVING count(*) >= 1"
            ),
        },
        "row_template": (
            "Mail: {c} permanent delivery failure(s) (bounced/expired) in the "
            "last 15m. These will NOT retry and have already left the queue, so "
            "the postfix queue alerts cannot see them. Trace: O2 -> stream "
            "mail_events -> status IN ('bounced','expired')."
        ),
        "creates_incident": False,
        "pending_period_sec": 0,
    },
    {
        "name": "mail_events_pipeline_coverage",
        "description": (
            "GUARD for mail_delivery_failed: proves the mail_events stream is "
            "still being fed AND still being parsed. Those pod logs are carved "
            "out of pod_logs by Cribl route mail_events_to_oo (namespace mail + "
            "pod docker-mailserver-* + container docker-mailserver) and shaped "
            "by the JS pipeline mail_events, so the stream is their ONLY home: "
            "if the route stops (mis-ordered, disabled, filter drift) or the "
            "pipeline stops running, mail_delivery_failed can only ever count "
            "0 and a real bounce outage becomes indistinguishable from a quiet "
            "day. FAILS CLOSED on both: an empty or dead stream fires the "
            "volume half (count(*) < 1000 against a measured 2283-6290/hour "
            "floor over 7 d), and a pipeline that runs but no longer derives "
            "fields fires the parse half (count(program) < 99% of rows; "
            "`program` comes from the pipeline's own "
            "'^program[pid]: text' regex and is 100% populated in every hour "
            "measured). BASELINE 2026-10-06 -- backtested over the 7 d replayed "
            "from pod_logs: exactly two hours out of 159 fire, 2026-10-03T08:00 "
            "(505 rows, recovery hour after the triple-host-down incident; its "
            "06:00/07:00 buckets are absent entirely) and 2026-10-05T21:00 (538 "
            "rows, the carve-out boundary itself, unrepeatable). Parse half: 0 "
            "hours fire across the 10 h of live mail_events. Modelled on "
            "pod_logs_service_name_coverage. Revert: DELETE "
            "/api/v2/default/alerts/<id>."
        ),
        "trigger_condition": {
            "period": 60,
            "operator": ">=",
            "threshold": 1,
            "frequency": 15,
            "frequency_type": "minutes",
            "cron": "",
            "silence": 240,
            "tolerance_in_secs": 0,
            "align_time": True,
        },
        "query_condition": {
            "type": "sql",
            "sql": (
                "SELECT count(*) AS total, count(program) AS prog "
                'FROM "mail_events" '
                "HAVING count(*) < 1000 OR count(program) < count(*) * 0.99"
            ),
        },
        "row_template": (
            "Mail log pipeline degraded: only {total} rows on stream mail_events "
            "in the last hour (normal 2283-6290), {prog} of them with a parsed "
            "program field. The Cribl route mail_events_to_oo or the mail_events "
            "JS pipeline has stopped feeding or stopped parsing this stream -- "
            "while that holds, mail_delivery_failed is blind and reads 0 on a "
            "real bounce. Check the Cribl route order and the pipeline's "
            "function log."
        ),
        "creates_incident": False,
        "pending_period_sec": 0,
    },
]

STREAM_NAME = "mail_events"


def _akeyless(name):
    """Fetch a secret from Akeyless using the LOCAL akeyless CLI.

    The gateway must be explicit: the CLI ignores the profile's gateway_url
    key, so a missing AKEYLESS_GATEWAY_URL fails as a confusing 404 on
    /api/derived-key rather than as a missing-variable error.
    """
    env = {**os.environ, "AKEYLESS_GATEWAY_URL": GATEWAY_URL}
    try:
        return subprocess.run(
            ["akeyless", "get-secret-value", "--name", name],
            capture_output=True, text=True, check=True, env=env,
        ).stdout.strip()
    except FileNotFoundError:
        raise SystemExit(
            "akeyless CLI not found on PATH. Run this from the jump seat "
            "(~/.local/bin/akeyless), which holds the credentials.")
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"akeyless get-secret-value --name {name} failed "
            f"(rc {e.returncode}): {(e.stderr or '').strip()[-300:]}")


def get_auth():
    pw = _akeyless("/mdapi/openobserve/o2/root-password")
    return base64.b64encode(f"tillo@tillo.ch:{pw}".encode()).decode()


def req(method, path, body=None, auth=None, base=None):
    url = f"{base or OO_URL}/api{path}"
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


def ensure_alert(auth, spec):
    payload = {
        "name": spec["name"],
        "description": spec["description"],
        "stream_name": STREAM_NAME,
        "stream_type": "logs",
        "destinations": [DESTINATION_NAME],
        "enabled": True,
        "is_real_time": False,
        "trigger_condition": spec["trigger_condition"],
        "query_condition": spec["query_condition"],
        "row_template": spec["row_template"],
        "row_template_type": "String",
        "creates_incident": spec.get("creates_incident", False),
        "pending_period_sec": spec.get("pending_period_sec", 0),
        "tz_offset": 0,
        "context_attributes": {},
        "workflows": [],
    }
    # List first: O2's alert POST is NOT an upsert -- it returns 200 and creates a
    # new alert even when a same-named alert already exists, so POSTing before
    # checking is how re-runs pile up duplicate alerts. PUT-by-id when the name
    # exists, POST only when absent (the list GET is a projection, but it still
    # carries name + alert_id, which is all we need here).
    code_get, alerts = req("GET", f"/v2/{ORG}/alerts", None, auth)
    existing_id = None
    if code_get == 200:
        for a in (alerts.get("list") or []):
            if a.get("name") == spec["name"]:
                existing_id = a.get("alert_id") or a.get("id")
                break
    if existing_id:
        code, r = req("PUT", f"/v2/{ORG}/alerts/{existing_id}", payload, auth)
        print(f"  alert: updated {spec['name']} (id={existing_id}) -> {code} {r}")
        return existing_id
    code, r = req("POST", f"/v2/{ORG}/alerts", payload, auth)
    print(f"  alert: created {spec['name']} -> {code} {r}")
    return (r or {}).get("id")


def verify(auth, alert_id, spec):
    """Read the alert back and assert the fields that matter actually landed."""
    code, d = req("GET", f"/v2/{ORG}/alerts/{alert_id}", None, auth)
    if code != 200:
        raise SystemExit(f"readback failed: {code} {d}")
    checks = {
        "stream_name": (d.get("stream_name"), STREAM_NAME),
        "stream_type": (d.get("stream_type"), "logs"),
        "destinations": (d.get("destinations"), [DESTINATION_NAME]),
        "enabled": (d.get("enabled"), True),
        "is_real_time": (d.get("is_real_time"), False),
        "query_condition.sql": (
            d["query_condition"]["sql"], spec["query_condition"]["sql"]),
        "trigger_condition.period": (
            d["trigger_condition"]["period"], spec["trigger_condition"]["period"]),
        "trigger_condition.silence": (
            d["trigger_condition"]["silence"], spec["trigger_condition"]["silence"]),
        "row_template": (d.get("row_template"), spec["row_template"]),
    }
    bad = [k for k, (got, want) in checks.items() if got != want]
    for k, (got, want) in checks.items():
        mark = "OK " if got == want else "BAD"
        print(f"  {mark} {k}: {got!r}")
    if bad:
        raise SystemExit(f"readback mismatch on: {', '.join(bad)}")
    print(f"  readback OK for {spec['name']}")


def _reachable(base):
    try:
        urllib.request.urlopen(f"{base}/api/default/alerts/templates", timeout=5)
        return True
    except urllib.error.HTTPError:
        return True          # answered, just not 200 -- the host is up
    except Exception:
        return False


def main():
    global OO_URL
    auth = get_auth()
    pf = None
    if not _reachable(OO_URL):
        print(f"{OO_URL} unreachable; falling back to a port-forward")
        pf = subprocess.Popen(
            ["kubectl", "--context", "mdapi-prod-direct", "-n", "openobserve",
             "port-forward", "svc/openobserve", f"{OO_PORT}:5080"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        OO_URL = f"http://127.0.0.1:{OO_PORT}"
        if not _reachable(OO_URL):
            pf.terminate()
            raise SystemExit("OpenObserve unreachable by DNS or port-forward")
    try:
        for spec in ALERTS:
            print(f"=== {spec['name']} ===")
            aid = ensure_alert(auth, spec)
            if not aid:
                raise SystemExit(f"no id returned for {spec['name']}")
            verify(auth, aid, spec)
    finally:
        if pf:
            pf.terminate()
            pf.wait(timeout=5)


if __name__ == "__main__":
    main()
