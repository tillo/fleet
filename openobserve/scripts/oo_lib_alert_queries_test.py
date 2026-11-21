#!/usr/bin/env python3
"""Tests for oo-lib-alert-queries.py (loaded by path: the name has hyphens).

    cd ~/fleet && python3 -m unittest discover -s openobserve/scripts -p '*_test.py'
"""
import contextlib
import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location(
    "oo_lib_alert_queries",
    pathlib.Path(__file__).with_name("oo-lib-alert-queries.py"))
q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q)

ETCD = "lib_etcd_peer_communication_failures"
LIBRARY_QUERY = "sum(rate(etcd_network_peer_sent_failures_total[5m])) by (To)"


def alert(name=ETCD, promql=LIBRARY_QUERY, alert_id="id-1"):
    return {"alert_id": alert_id, "name": name, "enabled": True,
            "query_condition": {"type": "promql", "promql": promql,
                          "promql_condition": {"column": "value",
                                               "operator": ">",
                                               "value": 0.01}},
            "trigger_condition": {"period": 2, "silence": 120}}


class EtcdPeerQuery(unittest.TestCase):
    """The pinned query's SHAPE is what keeps the alert from echoing a down member."""

    def setUp(self):
        self.pq = q.PINNED[ETCD]

    def test_groups_by_the_label_o2_actually_stores(self):
        ## O2 lowercases labels: `by (To)` collapses every peer into one
        ## unlabeled series, which is why the RCA could not name the member.
        self.assertIn("by (to)", self.pq)
        self.assertNotIn("To)", self.pq)

    def test_requires_the_target_member_continuously_up(self):
        self.assertIn('label_replace(count_over_time(etcd_server_id[10m]) >= 10, '
                      '"to", "$1", "server_id", "(.*)")', self.pq)

    def test_requires_failures_sustained_not_a_single_counter_step(self):
        self.assertTrue(self.pq.startswith("min_over_time(("))
        self.assertTrue(self.pq.endswith(")[7m:1m])"))

    def test_brackets_balance(self):
        self.assertEqual(self.pq.count("("), self.pq.count(")"))
        self.assertEqual(self.pq.count("["), self.pq.count("]"))


class Drift(unittest.TestCase):
    def test_library_query_is_drift(self):
        self.assertEqual(q.drift(alert(), q.PINNED[ETCD]), LIBRARY_QUERY)

    def test_pinned_query_is_not_drift(self):
        self.assertIsNone(q.drift(alert(promql=q.PINNED[ETCD]), q.PINNED[ETCD]))

    def test_missing_condition_is_drift(self):
        a = alert()
        del a["query_condition"]
        self.assertEqual(q.drift(a, "x"), "")


class Patched(unittest.TestCase):
    def test_replaces_only_the_promql(self):
        a = alert()
        out = q.patched(a, "NEW")
        self.assertEqual(out["query_condition"]["promql"], "NEW")
        self.assertEqual(out["query_condition"]["promql_condition"],
                         a["query_condition"]["promql_condition"])
        self.assertEqual(out["trigger_condition"], a["trigger_condition"])
        self.assertEqual(out["name"], ETCD)

    def test_does_not_mutate_the_input(self):
        a = alert()
        q.patched(a, "NEW")
        self.assertEqual(a["query_condition"]["promql"], LIBRARY_QUERY)

    def test_is_idempotent(self):
        once = q.patched(alert(), "NEW")
        self.assertEqual(q.patched(once, "NEW"), once)


class Plan(unittest.TestCase):
    def test_lists_only_drifted_pinned_alerts(self):
        rules = [alert(),
                 alert(name="other", alert_id="id-2"),
                 alert(name=ETCD, alert_id="id-3", promql=q.PINNED[ETCD])]
        self.assertEqual([(c["id"], c["name"]) for c in q.plan(rules)],
                         [("id-1", ETCD)])

    def test_missing_pinned_alert_is_reported(self):
        self.assertEqual(q.missing([alert(name="other")]), sorted(q.PINNED))

    def test_present_pinned_alert_is_not_missing(self):
        self.assertEqual(q.missing([alert()]), [])


class FakeO2:
    """Stands in for oo-alert-policy.py: an O2 holding `rules`, recording PUTs."""
    ORG = "default"

    def __init__(self, rules, honour_put=True):
        self.rules, self.honour_put, self.puts = rules, honour_put, []

    def auth(self):
        return {"Authorization": "x"}

    def load_rules(self, hdr):
        return [dict(r) for r in self.rules]

    def api(self, method, path, hdr, body=None):
        self.puts.append((method, path, body))
        if self.honour_put:
            self.rules = [body if r["alert_id"] == body["alert_id"] else r
                          for r in self.rules]
        return {}


class Cli(unittest.TestCase):
    def run_main(self, fake, cmd):
        real = q._policy
        q._policy = lambda: fake
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = q.main([cmd])
        finally:
            q._policy = real
        return rc, out.getvalue()

    def test_verify_flags_drift(self):
        rc, out = self.run_main(FakeO2([alert()]), "verify")
        self.assertEqual(rc, 1)
        self.assertIn("DRIFT " + ETCD, out)

    def test_verify_flags_a_missing_pinned_rule(self):
        rc, out = self.run_main(FakeO2([alert(name="other")]), "verify")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING " + ETCD, out)

    def test_verify_clean(self):
        rc, out = self.run_main(FakeO2([alert(promql=q.PINNED[ETCD])]), "verify")
        self.assertEqual(rc, 0)
        self.assertIn("0 drift(s)", out)

    def test_verify_writes_nothing(self):
        fake = FakeO2([alert()])
        self.run_main(fake, "verify")
        self.assertEqual(fake.puts, [])

    def test_apply_converges_and_writes_only_the_drifted_rule(self):
        fake = FakeO2([alert(), alert(name="other", alert_id="id-2")])
        rc, out = self.run_main(fake, "apply")
        self.assertEqual(rc, 0)
        self.assertIn("converged", out)
        self.assertEqual([(m, p) for m, p, _ in fake.puts],
                         [("PUT", "/api/v2/default/alerts/id-1")])
        self.assertEqual(fake.puts[0][2]["query_condition"]["promql"], q.PINNED[ETCD])

    def test_apply_twice_writes_once(self):
        fake = FakeO2([alert()])
        self.run_main(fake, "apply")
        self.run_main(fake, "apply")
        self.assertEqual(len(fake.puts), 1)

    def test_apply_reports_failure_when_the_write_did_not_land(self):
        rc, out = self.run_main(FakeO2([alert()], honour_put=False), "apply")
        self.assertEqual(rc, 1)
        self.assertIn("STILL DRIFTED", out)


if __name__ == "__main__":
    unittest.main()
