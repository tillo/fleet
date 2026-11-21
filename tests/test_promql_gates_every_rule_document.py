#!/usr/bin/env python3
"""Every PrometheusRule is inside the directory promtool checks, and CI runs it.

WHY THIS FILE EXISTS (2026-11-08)
`(a - b) offset 24h` is MetricsQL. vmalert-tool — the rule gate this repo already had —
accepts it, because MetricsQL is a SUPERSET of PromQL. So the estate's own gates were
green, the MR merged, and the prometheus-operator webhook (the only place Prometheus'
parser runs) refused the file. Fleet's Helm release rolled back and `app-batch-jobs` —
211 alerts including BatchJobStale and PushgatewayGroupStale — was DELETED from the
cluster and could not be recreated, for ~20 minutes, with nothing paging about a dead
batch job.

tools/ci/promql-rules.py now runs promtool over every tracked `monitoring-rules/*.yml`.
This test is the half that keeps that true: the gate is worthless if a rule file can be
committed outside the directory it scans, or if the CI step is dropped as noise.

⚠️ What this does NOT check: whether the expressions are valid — that needs promtool and
runs in CI. This test is deliberately binary-free so it can run in the 15-second gate.
"""

from __future__ import annotations

import unittest

import yaml

import fleetlib

CI_FILE = ".gitlab-ci.yml"
TOOL = "tools/ci/promql-rules.py"
RULE_DIR = "monitoring-rules/"


class PromqlGateCoversEveryRuleDocument(unittest.TestCase):

    def test_ci_runs_the_promql_gate(self):
        text = (fleetlib.repo_root() / CI_FILE).read_text(encoding="utf-8")
        self.assertIn(TOOL, text,
                      "fleet-validate no longer runs %s: a rule Prometheus cannot parse "
                      "would again roll the whole rule file back behind a green pipeline"
                      % TOOL)

    def test_every_prometheus_rule_lives_where_the_gate_looks(self):
        outside = []
        for rel in fleetlib.tracked_yaml():
            if rel.startswith(RULE_DIR):
                continue
            for doc in fleetlib.load_docs(rel):
                if isinstance(doc, dict) and doc.get("kind") == "PrometheusRule":
                    outside.append(rel)
        self.assertEqual(outside, [],
                         "PrometheusRule documents outside %s are never checked by %s: %s"
                         % (RULE_DIR, TOOL, sorted(set(outside))))

    def test_the_rule_directory_actually_holds_rules(self):
        # A gate that scans an empty directory passes silently and proves nothing.
        docs = [d for rel in fleetlib.tracked_yaml() if rel.startswith(RULE_DIR)
                for d in fleetlib.load_docs(rel)
                if isinstance(d, dict) and d.get("kind") == "PrometheusRule"]
        self.assertGreater(len(docs), 50, "only %d PrometheusRule documents found" % len(docs))

    def test_every_rule_carries_an_expression(self):
        # promtool rejects a rule without `expr`; catching it here names the file, where
        # promtool's message would point into a scratch copy.
        for rel in fleetlib.tracked_yaml():
            if not rel.startswith(RULE_DIR):
                continue
            for doc in fleetlib.load_docs(rel):
                if not (isinstance(doc, dict) and doc.get("kind") == "PrometheusRule"):
                    continue
                for group in (doc.get("spec") or {}).get("groups") or []:
                    for rule in group.get("rules") or []:
                        with self.subTest(file=rel, group=group.get("name")):
                            self.assertTrue(rule.get("expr"),
                                            "%s: a rule in %r has no expr"
                                            % (rel, group.get("name")))


if __name__ == "__main__":
    unittest.main()
