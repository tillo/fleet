#!/usr/bin/env python3
"""Alert rules have unit tests that CI actually runs (infra-r9mzd.3).

WHY THIS FILE EXISTS (2026-11-04)
fleet !1812 muted the Mail SyntheticProbeFailing page for 10 min after a docker-mailserver
pod creation. Its proof was a vmalert-tool unit test run by hand on the seat: green on the
new rule, red on the old one. Nothing in CI ran it, so the next edit of that expression
could silently re-arm the page on every rollout, or worse, mute real outages. A test file
nobody runs is a comment; tools/ci/rule-tests.py now runs every tests/rules/*.test.yml in
fleet-validate against the PrometheusRule files the cluster actually loads.

WHAT IS ASSERTED
  1. the runner hands vmalert-tool exactly `spec.groups` of each PrometheusRule (the CRD
     wrapper is not a vmalert rule file);
  2. a test's rule_files are rewritten to those extracted copies, nothing else changes;
  3. every tests/rules/*.test.yml names at least one rule file, and each is a tracked
     monitoring-rules PrometheusRule -- a renamed rule file must fail here, not pass by
     testing nothing.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).absolute().parent.parent
_spec = importlib.util.spec_from_file_location("rule_tests", ROOT / "tools" / "ci" / "rule-tests.py")
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

RULE = {
    "apiVersion": "monitoring.coreos.com/v1",
    "kind": "PrometheusRule",
    "metadata": {"name": "x", "namespace": "monitoring"},
    "spec": {"groups": [{"name": "g", "rules": [{"alert": "A", "expr": "up == 0"}]}]},
}


class Extract(unittest.TestCase):
    def test_returns_only_the_groups(self):
        self.assertEqual(rt.vmalert_rule_file(RULE), {"groups": RULE["spec"]["groups"]})

    def test_rejects_a_non_prometheusrule(self):
        with self.assertRaises(ValueError):
            rt.vmalert_rule_file({"kind": "ConfigMap", "spec": {"groups": []}})


class Prepare(unittest.TestCase):
    def test_rewrites_rule_files_to_extracted_copies(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            (root / "monitoring-rules" / "r.yml").write_text(yaml.safe_dump(RULE))
            test = {"rule_files": ["monitoring-rules/r.yml"], "tests": [{"name": "t"}]}
            got = rt.prepare(test, root, out)
            self.assertEqual(got["tests"], [{"name": "t"}])
            self.assertEqual(len(got["rule_files"]), 1)
            extracted = yaml.safe_load(Path(got["rule_files"][0]).read_text())
            self.assertEqual(extracted, {"groups": RULE["spec"]["groups"]})
            self.assertEqual(test["rule_files"], ["monitoring-rules/r.yml"])  # input untouched

    def test_two_rule_files_get_two_distinct_copies(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            for n in ("a.yml", "b.yml"):
                (root / "monitoring-rules" / n).write_text(yaml.safe_dump(RULE))
            got = rt.prepare({"rule_files": ["monitoring-rules/a.yml", "monitoring-rules/b.yml"]}, root, out)
            self.assertEqual(len(set(got["rule_files"])), 2)


class EveryRuleTestPointsAtARealRule(unittest.TestCase):
    def test_rule_tests_exist_and_name_tracked_prometheusrules(self):
        tests = sorted((ROOT / "tests" / "rules").glob("*.test.yml"))
        self.assertTrue(tests, "no tests/rules/*.test.yml at all")
        for t in tests:
            with self.subTest(test=t.name):
                files = yaml.safe_load(t.read_text()).get("rule_files") or []
                self.assertTrue(files, "names no rule file")
                for f in files:
                    self.assertTrue(f.startswith("monitoring-rules/"), f)
                    doc = yaml.safe_load((ROOT / f).read_text())
                    self.assertEqual(doc.get("kind"), "PrometheusRule", f)


if __name__ == "__main__":
    unittest.main()
