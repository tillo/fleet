#!/usr/bin/env python3
"""
Pin the PromQL of imported O2 Alert-Library rules whose library query is wrong here.

  python3 openobserve/scripts/oo-lib-alert-queries.py verify   # exit 1 on drift
  python3 openobserve/scripts/oo-lib-alert-queries.py apply    # converge, then re-verify

The alert-library rules were imported once into O2's database (2026-09-24) and have
no other source of truth, so a query fixed by hand in the UI is untracked and lost on
any re-import. PINNED is the one place a library query is overridden on this estate;
oo-alert-policy.py owns the notification side (silence, destination priority).

lib_etcd_peer_communication_failures (infra-ahql2, 2026-11-20)
---------------------------------------------------------------
Library: sum(rate(etcd_network_peer_sent_failures_total[5m])) by (To) > 0.01
Measured over 30 d (O2, 1-min steps): 946 firing-minutes in 22 episodes, and EVERY one
was peers failing to send to a member that was DOWN (planned node reboots, the 1.9
upgrade, a node crash). VictoriaMetrics agrees independently. That is a member-down
echo, which EtcdMemberMissing (vmalert) and lib_etcd_insufficient_members already page.
The alert's own meaning, a partition between LIVE members, never occurred.
Two defects:
  1. O2 stores the label lowercase (`to`), so `by (To)` collapsed all peers into one
     unlabeled series: the incident could never name the member.
  2. No gate on the target being up, and a single counter step (a rejoin) is echoed
     by the [5m] rate window for ~4 min.
Pinned query: failures to a member whose etcd_server_id was scraped in all 10 of the
last 10 minutes (one sample/min on the deduped O2 feed), sustained for 7 min. Backtest:
946 -> 1 firing-minute (10-26 07:40, a 4-sample rejoin tail). Today's three live members
all pass the gate, so the alert is not blinded. A sender-side gate was measured and
added nothing, so it is not here.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import pathlib
import sys

_ETCD_TARGET_UP_10M = ('label_replace(count_over_time(etcd_server_id[10m]) >= 10, '
                       '"to", "$1", "server_id", "(.*)")')

PINNED: dict[str, str] = {
    "lib_etcd_peer_communication_failures":
        "min_over_time(("
        "sum by (to) (rate(etcd_network_peer_sent_failures_total[5m]))"
        f" and on (to) {_ETCD_TARGET_UP_10M}"
        ")[7m:1m])",
}


# ---------------------------------------------------------------- pure logic

def drift(rule: dict, want: str) -> str | None:
    """None when the rule already carries `want`, else the query it has now."""
    got = (rule.get("query_condition") or {}).get("promql") or ""
    return None if got == want else got


def patched(rule: dict, promql: str) -> dict:
    """A copy of the rule with only its PromQL replaced."""
    out = copy.deepcopy(rule)
    out.setdefault("query_condition", {})["promql"] = promql
    return out


def plan(rules: list[dict]) -> list[dict]:
    """The pinned rules whose live query differs, as {id, name, was, want}."""
    out = []
    for r in rules:
        want = PINNED.get(r.get("name"))
        if want is None:
            continue
        was = drift(r, want)
        if was is not None:
            out.append({"id": r["alert_id"], "name": r["name"], "was": was, "want": want})
    return out


def missing(rules: list[dict]) -> list[str]:
    """Pinned names with no live rule: a pin on nothing is a silent no-op."""
    names = {r.get("name") for r in rules}
    return sorted(n for n in PINNED if n not in names)


# ---------------------------------------------------------------------- I/O
## Auth and the alert listing are oo-alert-policy.py's; reuse, never copy.

def _policy():
    spec = importlib.util.spec_from_file_location(
        "oo_alert_policy", pathlib.Path(__file__).with_name("oo-alert-policy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify(rules: list[dict]) -> int:
    bad = 0
    for n in missing(rules):
        print(f"MISSING {n}: pinned but not in O2")
        bad += 1
    for c in plan(rules):
        print(f"DRIFT {c['name']}:\n  live: {c['was']}\n  want: {c['want']}")
        bad += 1
    print(f"{len(PINNED)} pinned quer{'y' if len(PINNED) == 1 else 'ies'}; {bad} drift(s)")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("cmd", choices=["verify", "apply"])
    a = ap.parse_args(argv)

    pol = _policy()
    hdr = pol.auth()
    rules = pol.load_rules(hdr)
    if a.cmd == "verify":
        return verify(rules)
    for c in plan(rules):
        rule = next(r for r in rules if r["alert_id"] == c["id"])
        pol.api("PUT", f"/api/v2/{pol.ORG}/alerts/{c['id']}", hdr, patched(rule, c["want"]))
        print(f"set   {c['name']}")
    ## A 200 is not proof: re-read and judge with the same predicate.
    left = verify(pol.load_rules(hdr))
    print("converged" if not left else "STILL DRIFTED after apply")
    return left


if __name__ == "__main__":
    sys.exit(main())
