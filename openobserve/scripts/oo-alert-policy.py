#!/usr/bin/env python3
"""
Bring OpenObserve's alert NOTIFICATION policy under control and under git.

  python3 openobserve/scripts/oo-alert-policy.py show       # what is live
  python3 openobserve/scripts/oo-alert-policy.py verify     # exit 1 on drift
  python3 openobserve/scripts/oo-alert-policy.py apply      # converge
  python3 openobserve/scripts/oo-alert-policy.py snapshot   # version the rules

WHY (2026-11-08, Pushover pool)
--------------------------------
The estate's Pushover quota is ONE account-wide pool of 10,000/month shared by
~24 applications. It was at 6,394 with 22 days left in November. Measuring the
senders turned up two things nothing owned:

1. ⛔ The `pushover_mdapi` destination template carries NO `priority` key, so
   every O2 notification goes out at Pushover's default priority 0 — audible.
   63 of the 81 O2 alert rules forward to it, ~96 firings/day. That bypasses the
   estate's severity tiering entirely (Alertmanager sends criticals at 1,
   warnings at -1, info at -2). O2 was shouting everything.
2. The rules live only in O2's database. `mail/`, `technitium/` and the dmarc
   scripts create a handful from git; the other ~78 have no source of truth, so
   any edit is untracked drift and no review sees it. `snapshot` fixes that.

POLICY encoded here
-------------------
  * destination priority = -1 (quiet: no sound, no vibration). Audible paging
    stays with Alertmanager, which is severity-tiered. O2 is the correlation and
    incident channel, not the pager.
  * every pushover-forwarding rule re-notifies no more often than
    TARGET_SILENCE_MIN minutes. First notification is always immediate; this
    only bounds the REPEAT rate of a sustained condition.

⚠️ `silence` is in MINUTES, not seconds. The field carries no unit in the JSON
and the OpenAPI schema is the only place it is stated
(`TriggerCondition.properties.silence.description == "(minutes)"`). Reading it
as seconds overstates the repeat rate by 60x — measured wrong once already.

Idempotent: `apply` writes only what `verify` calls drift, then reads back.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.request

OO_URL = "http://openobserve.openobserve.svc.cluster.local:5080"
ORG = "default"
TEMPLATE = "pushover_mdapi"
AK_PASSWORD = "/mdapi/openobserve/o2/root-password"
OO_USER = "tillo@tillo.ch"

## Quiet. Not -2: -2 is badge-only and these are still incidents worth seeing.
TARGET_PRIORITY = -1
TARGET_SILENCE_MIN = 120


# ---------------------------------------------------------------- pure logic

def target_silence(current: int | None) -> int:
    """The silence a rule should carry. Only ever RAISED, never lowered.

    A rule already quieter than the target is left alone: this tool bounds the
    repeat rate, it does not have an opinion about being stricter than policy.
    """
    return max(int(current or 0), TARGET_SILENCE_MIN)


def is_pushover(rule: dict) -> bool:
    return TEMPLATE in (rule.get("destinations") or [])


def silence_of(rule: dict) -> int:
    return int((rule.get("trigger_condition") or {}).get("silence") or 0)


def plan(rules: list[dict]) -> list[dict]:
    """The rules that need a silence bump, as {name, id, was, want}."""
    out = []
    for r in rules:
        if not r.get("enabled") or not is_pushover(r):
            continue
        want = target_silence(silence_of(r))
        if want != silence_of(r):
            out.append({"id": r["alert_id"], "name": r.get("name", "?"),
                        "was": silence_of(r), "want": want})
    return out


def priority_problem(template: dict) -> str | None:
    """None when the destination is quiet enough, else the reason."""
    body = template.get("body") or {}
    got = body.get("priority")
    if got is None:
        return "template has NO priority key -> Pushover default 0 (audible)"
    if int(got) > TARGET_PRIORITY:
        return f"template priority {got} is louder than {TARGET_PRIORITY}"
    return None


# ---------------------------------------------------------------------- I/O

def _akeyless(name: str) -> str:
    try:
        p = subprocess.run(["akeyless", "get-secret-value", "--name", name],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise SystemExit("akeyless CLI not found; run this from the jump seat")
    if p.returncode != 0:
        raise SystemExit(f"akeyless get-secret-value --name {name} failed: "
                         f"{p.stderr.strip()[:200]}")
    return p.stdout.strip()


def auth() -> dict:
    pw = _akeyless(AK_PASSWORD)
    tok = base64.b64encode(f"{OO_USER}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


def api(method: str, path: str, hdr: dict, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{OO_URL}{path}", data=data, headers=hdr,
                                 method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def load_rules(hdr: dict) -> list[dict]:
    """Every alert with its destinations, which the list endpoint omits."""
    out = []
    for a in api("GET", f"/api/v2/{ORG}/alerts", hdr)["list"]:
        full = api("GET", f"/api/v2/{ORG}/alerts/{a['alert_id']}", hdr)
        ## The detail endpoint does NOT echo alert_id (it is the path segment),
        ## so carry it across or every plan() lookup raises KeyError.
        full["alert_id"] = a["alert_id"]
        out.append(full)
    return out


def load_template(hdr: dict) -> dict:
    tpl = api("GET", f"/api/{ORG}/alerts/templates/{TEMPLATE}", hdr)
    raw = tpl.get("body")
    tpl["body"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return tpl


# ------------------------------------------------------------------- actions

def show(rules: list[dict]) -> None:
    print(f"{'rule':50} {'on':3} {'push':5} {'silence':>8}  max/day")
    print("-" * 84)
    for r in sorted(rules, key=lambda x: x.get("name") or ""):
        s = silence_of(r)
        per = 1440 // s if s else 0
        print(f"{(r.get('name') or '?')[:50]:50} "
              f"{'y' if r.get('enabled') else 'n':3} "
              f"{'y' if is_pushover(r) else '-':5} {s:>8}  "
              f"{per if s else 'every eval'}")


def verify(rules: list[dict], tpl: dict) -> int:
    bad = 0
    prob = priority_problem(tpl)
    if prob:
        print(f"DRIFT destination {TEMPLATE}: {prob}")
        bad += 1
    else:
        print(f"ok    destination {TEMPLATE}: priority "
              f"{tpl['body'].get('priority')} (quiet)")
    for c in plan(rules):
        print(f"DRIFT {c['name']}: silence {c['was']} < {c['want']} min")
        bad += 1
    quiet = [r for r in rules if r.get("enabled") and is_pushover(r)]
    print(f"\n{len(quiet)} enabled rules forward to {TEMPLATE}; {bad} drift(s)")
    return 1 if bad else 0


def apply(rules: list[dict], tpl: dict, hdr: dict) -> int:
    changed = 0
    if priority_problem(tpl):
        body = dict(tpl["body"])
        body["priority"] = TARGET_PRIORITY
        payload = {k: v for k, v in tpl.items() if k != "body"}
        payload["body"] = json.dumps(body)
        api("PUT", f"/api/{ORG}/alerts/templates/{TEMPLATE}", hdr, payload)
        changed += 1
        print(f"set   destination priority -> {TARGET_PRIORITY}")

    for c in plan(rules):
        rule = next(r for r in rules if r["alert_id"] == c["id"])
        tc = dict(rule.get("trigger_condition") or {})
        tc["silence"] = c["want"]
        payload = {k: v for k, v in rule.items() if k != "trigger_condition"}
        payload["trigger_condition"] = tc
        api("PUT", f"/api/v2/{ORG}/alerts/{c['id']}", hdr, payload)
        changed += 1
        print(f"set   {c['name']}: silence {c['was']} -> {c['want']} min")

    print(f"\n{changed} change(s) written")
    return changed


def snapshot(rules: list[dict], tpl: dict, path: str) -> None:
    doc = {"_note": "Live OpenObserve alert policy, captured by "
                    "openobserve/scripts/oo-alert-policy.py snapshot. "
                    "Credentials are NOT included.",
           "destination": {"name": TEMPLATE,
                           "priority": tpl["body"].get("priority")},
           "rules": [{"name": r.get("name"), "enabled": r.get("enabled"),
                      "destinations": r.get("destinations") or [],
                      "silence_min": silence_of(r),
                      "frequency": (r.get("trigger_condition") or {}).get("frequency"),
                      "frequency_type": (r.get("trigger_condition") or {}).get("frequency_type"),
                      "severity": r.get("severity")}
                     for r in sorted(rules, key=lambda x: x.get("name") or "")]}
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(rules)} rules -> {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("cmd", choices=["show", "verify", "apply", "snapshot"])
    ap.add_argument("--out", default="openobserve/o2-alert-policy.json",
                    help="snapshot destination (repo-relative)")
    a = ap.parse_args(argv)

    hdr = auth()
    rules = load_rules(hdr)
    tpl = load_template(hdr)
    if a.cmd == "show":
        show(rules)
        return 0
    if a.cmd == "verify":
        return verify(rules, tpl)
    if a.cmd == "snapshot":
        snapshot(rules, tpl, a.out)
        return 0
    apply(rules, tpl, hdr)
    ## A 200 is not proof: re-read and report against the same predicate.
    rules2, tpl2 = load_rules(hdr), load_template(hdr)
    left = verify(rules2, tpl2)
    print("converged" if not left else "STILL DRIFTED after apply")
    return left


if __name__ == "__main__":
    sys.exit(main())
