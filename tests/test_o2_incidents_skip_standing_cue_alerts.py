"""A standing INFO cue must not reach O2 Incidents, where it would hold a real incident open.

Incident (infra-2a3i3, 2026-11-12): O2 correlates every alert into incidents by
namespace + service, and the incident keeps the title of the alert that opened it.
DeepSeekBalanceFloor (`deepseek_balance_usd < 10`, severity info) is by design a
cue that stays on for days ("It is not an incident", 58-llm-spend.yml). It shares
service=deepseek / namespace=litellm with DeepSeekBalanceLow. So after the burn
forecast stopped firing on 11-09 12:40Z, the incident "DeepSeekBalanceLow on
deepseek" (3Lp8vC0F8fZbn9mIWOmvptPAA3K) stayed OPEN on the floor alert's 2-hourly
re-notifications: 40 of its 52 alerts were DeepSeekBalanceFloor. (The floor rule
itself was retired 2026-11-13 as a duplicate of DeepSeekBalanceLow, so its drop
route went with it; the test now guards that no dead route is left behind.) The o2_incident_sync
row therefore never went quiet, and its triage read "fix-not-holding" against a
burn-rate fix (infra-r9mzd.42) that was in fact holding.

Same shape as the 2026-11-08 LonghornVolumeDegraded info exclusion: match the
severity, never the alertname alone, so a higher tier of the same rule still
reaches O2. Pushover's badge-only info tier (mdapi-team-vmac.yml) is a separate
route and is not touched.

The test walks the real route tree with the real rule labels, the way Alertmanager
does: the parent matchers must match, the first matching child wins, else the parent.
"""
from __future__ import annotations

import re
import unittest

from fleetlib import load_docs

VMAC = "monitoring-vmalertmanager-config/o2-incidents-vmac.yml"
RULES = "monitoring-rules/58-llm-spend.yml"
O2, DROP = "o2-incidents", "black-hole"
MATCHER = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!~|!=|=)\s*"(.*)"\s*$')


def matches(matcher: str, labels: dict[str, str]) -> bool:
    m = MATCHER.match(matcher)
    if not m:
        raise ValueError(f"unparsed matcher {matcher!r}")
    name, op, want = m.groups()
    have = labels.get(name, "")
    if op in ("=~", "!~"):
        hit = re.fullmatch(want, have) is not None
        return hit if op == "=~" else not hit
    return (have == want) if op == "=" else (have != want)


def receiver_for(route: dict, labels: dict[str, str]) -> str | None:
    """None when the route does not take the alert at all."""
    if not all(matches(m, labels) for m in route.get("matchers") or []):
        return None
    for child in route.get("routes") or []:
        got = receiver_for(child, labels)
        if got is not None:
            return got
    return route["receiver"]


def o2_route() -> dict:
    docs = [d for d in load_docs(VMAC) if d.get("kind") == "VMAlertmanagerConfig"]
    assert len(docs) == 1, f"{VMAC}: expected one VMAlertmanagerConfig, found {len(docs)}"
    return docs[0]["spec"]["route"]


def rule_labels(alert: str, severity: str) -> dict[str, str]:
    """The rule's static labels plus alertname; templated values are left out."""
    for doc in load_docs(RULES):
        for group in (doc.get("spec") or {}).get("groups") or []:
            for rule in group.get("rules") or []:
                labels = rule.get("labels") or {}
                if rule.get("alert") == alert and labels.get("severity") == severity:
                    static = {k: str(v) for k, v in labels.items() if "{{" not in str(v)}
                    return {**static, "alertname": alert}
    raise AssertionError(f"{RULES}: no rule {alert} with severity {severity}")


class O2IncidentsSkipStandingCueAlerts(unittest.TestCase):
    def test_route_tree_is_found(self):
        route = o2_route()
        self.assertTrue(route.get("matchers"), "O2 route lost its matchers")
        self.assertTrue(route.get("routes"), "O2 route has no exclusion children")

    def test_retired_deepseek_floor_leaves_no_dead_route(self):
        """DeepSeekBalanceFloor was retired 2026-11-13: no rule may define it, so a
        black-hole child naming it would only be a dead matcher left to rot."""
        with self.assertRaises(AssertionError):
            rule_labels("DeepSeekBalanceFloor", "info")
        named = [m for child in o2_route().get("routes") or []
                 for m in child.get("matchers") or [] if "DeepSeekBalanceFloor" in m]
        self.assertEqual(named, [], "O2 route still names the retired DeepSeekBalanceFloor")

    def test_deepseek_burn_forecast_still_reaches_o2(self):
        labels = rule_labels("DeepSeekBalanceLow", "warning")
        self.assertEqual(receiver_for(o2_route(), labels), O2)

    def test_deepseek_probe_failure_still_reaches_o2(self):
        labels = rule_labels("DeepSeekBalanceProbeFailing", "warning")
        self.assertEqual(receiver_for(o2_route(), labels), O2)

    def test_longhorn_degraded_tiers_keep_their_routes(self):
        base = {"team": "mdapi", "alertname": "LonghornVolumeDegraded"}
        self.assertEqual(receiver_for(o2_route(), {**base, "severity": "info"}), DROP)
        self.assertEqual(receiver_for(o2_route(), {**base, "severity": "warning"}), O2)

    def test_matcher_operators(self):
        labels = {"a": "x"}
        self.assertTrue(matches('a="x"', labels))
        self.assertFalse(matches('a!="x"', labels))
        self.assertTrue(matches('a=~"x|y"', labels))
        self.assertFalse(matches('a=~"xx"', labels))
        self.assertTrue(matches('a!~"y"', labels))
        self.assertFalse(matches('b="x"', labels))
        with self.assertRaises(ValueError):
            matches("a ~ x", labels)


if __name__ == "__main__":
    unittest.main()
