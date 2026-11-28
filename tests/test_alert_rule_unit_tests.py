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
import contextlib
import io
import os
import unittest
from unittest import mock
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

    def test_a_multi_document_file_yields_its_prometheusrule_groups(self):
        # monitoring-rules/15b-*.yml carries a ServiceMonitor in front of its rules
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            sm = {"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "spec": {}}
            (root / "monitoring-rules" / "m.yml").write_text(yaml.safe_dump_all([sm, RULE]))
            got = rt.prepare({"rule_files": ["monitoring-rules/m.yml"]}, root, out)
            extracted = yaml.safe_load(Path(got["rule_files"][0]).read_text())
            self.assertEqual(extracted, {"groups": RULE["spec"]["groups"]})

    def test_a_file_without_a_prometheusrule_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            sm = {"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "spec": {}}
            (root / "monitoring-rules" / "m.yml").write_text(yaml.safe_dump_all([sm]))
            with self.assertRaises(ValueError):
                rt.prepare({"rule_files": ["monitoring-rules/m.yml"]}, root, out)

    def test_a_file_with_two_prometheusrules_is_refused(self):
        # vmalert-tool would test only one of them; ambiguity must fail, not pick
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            (root / "monitoring-rules" / "m.yml").write_text(yaml.safe_dump_all([RULE, RULE]))
            with self.assertRaises(ValueError):
                rt.prepare({"rule_files": ["monitoring-rules/m.yml"]}, root, out)

    def test_a_nested_output_dir_is_created_and_reuse_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "a" / "b" / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            (root / "monitoring-rules" / "r.yml").write_text(yaml.safe_dump(RULE))
            test = {"rule_files": ["monitoring-rules/r.yml"]}
            rt.prepare(test, root, out)
            got = rt.prepare(test, root, out)
            self.assertTrue(Path(got["rule_files"][0]).is_file())

    def test_two_rule_files_get_two_distinct_copies(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "repo", Path(d) / "out"
            (root / "monitoring-rules").mkdir(parents=True)
            for n in ("a.yml", "b.yml"):
                (root / "monitoring-rules" / n).write_text(yaml.safe_dump(RULE))
            got = rt.prepare({"rule_files": ["monitoring-rules/a.yml", "monitoring-rules/b.yml"]}, root, out)
            self.assertEqual(len(set(got["rule_files"])), 2)


FAKE_TOOL = """#!/bin/sh
# fake vmalert-tool: logs start/end, prints its file, fails for files named *bad*
# timestamps via python: busybox date (the CI image is alpine) has no %N
now() { python3 -c 'import time; print(time.time())'; }
f="$3"; echo "start $(now)" >> "$FAKE_LOG.$$"
echo "testing $f"; sleep 0.4; echo "done $f"
echo "end $(now)" >> "$FAKE_LOG.$$"
case "$f" in *bad*) exit 1;; esac
"""


class WorkerCount(unittest.TestCase):
    def test_the_setting_wins(self):
        self.assertEqual(rt.worker_count("3", 32), 3)

    def test_without_a_setting_the_cpus_decide(self):
        self.assertEqual(rt.worker_count(None, 6), 6)
        self.assertEqual(rt.worker_count("", 6), 6)

    def test_without_cpus_one(self):
        self.assertEqual(rt.worker_count(None, None), 1)


class RunAll(unittest.TestCase):
    """2026-11-24: the 59 files ran one after another at ~14 s each -- 819 s of every
    fleet-validate run. They are independent (vmalert-tool binds a random free port and
    keeps its storage in memory), so they run concurrently; the log stays readable because
    each file's output is printed as one block, in file order."""

    def _repo(self, d: Path, names: list[str]) -> Path:
        root = d / "repo"
        (root / "monitoring-rules").mkdir(parents=True)
        (root / "tests" / "rules").mkdir(parents=True)
        (root / "monitoring-rules" / "r.yml").write_text(yaml.safe_dump(RULE))
        for n in names:
            (root / "tests" / "rules" / f"{n}.test.yml").write_text(
                yaml.safe_dump({"rule_files": ["monitoring-rules/r.yml"], "tests": []}))
        tool = d / "tool"
        tool.write_text(FAKE_TOOL)
        tool.chmod(0o755)
        os.environ["FAKE_LOG"] = str(d / "log")
        return root

    def test_reports_exactly_the_failing_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), ["a", "bad-b", "c"])
            failed, _, _ = rt.run_all(root, str(Path(d) / "tool"), jobs=3)
            self.assertEqual(failed, ["bad-b.test.yml"])

    def test_files_run_concurrently(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), ["a", "b", "c", "e"])
            rt.run_all(root, str(Path(d) / "tool"), jobs=4)
            spans = []
            for log in Path(d).glob("log.*"):
                t = dict(line.split() for line in log.read_text().splitlines())
                spans.append((float(t["start"]), float(t["end"])))
            self.assertEqual(len(spans), 4)
            latest_start = max(s for s, _ in spans)
            earliest_end = min(e for _, e in spans)
            self.assertLess(latest_start, earliest_end, "no two files overlapped")

    def test_each_file_output_is_one_block_in_file_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), ["a", "b", "c"])
            _, log, _ = rt.run_all(root, str(Path(d) / "tool"), jobs=3)
            heads = [line for line in log.splitlines() if line.startswith("== ")]
            self.assertEqual(heads, [f"== tests/rules/{n}.test.yml" for n in "abc"])
            for block in log.split("== ")[1:]:
                lines = block.splitlines()
                self.assertIn("testing", lines[1])
                self.assertIn("done", lines[2])

    def _main(self, root: Path, tool: Path) -> tuple[int, str]:
        out = io.StringIO()
        env = {"VMALERT_TOOL": str(tool), "RULE_TEST_JOBS": "2"}
        with mock.patch.object(rt, "ROOT", root), mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return rt.main(), out.getvalue()

    def test_main_exits_nonzero_and_names_the_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), ["a", "bad-b"])
            rc, out = self._main(root, Path(d) / "tool")
            self.assertEqual(rc, 1)
            self.assertIn("1/2 rule test files passed; FAILED: bad-b.test.yml", out)

    def test_main_exits_zero_when_every_file_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), ["a", "b"])
            rc, out = self._main(root, Path(d) / "tool")
            self.assertEqual(rc, 0)
            self.assertTrue(out.rstrip().endswith("2/2 rule test files passed"), out)

    def test_main_with_no_test_files_fails(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._main(self._repo(Path(d), []), Path(d) / "tool")
            self.assertEqual(rc, 1)
            self.assertIn("no tests/rules", out)

    def test_no_test_files_is_a_failure_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(Path(d), [])
            with self.assertRaises(FileNotFoundError):
                rt.run_all(root, str(Path(d) / "tool"), jobs=2)


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
                    kinds = [d.get("kind") for d in yaml.safe_load_all((ROOT / f).read_text()) if d]
                    self.assertIn("PrometheusRule", kinds, f)


class EveryInputSeriesIsAValueString(unittest.TestCase):
    """An unquoted `values: 0x90` is YAML 1.1 hex: ONE sample of 144, not 91 zeros. It goes
    stale after 5 min, so a "zero stays silent" case passes because the series is absent."""

    def test_input_series_values_are_strings(self):
        for t in sorted((ROOT / "tests" / "rules").glob("*.test.yml")):
            for case in yaml.safe_load(t.read_text()).get("tests") or []:
                for s in case.get("input_series") or []:
                    with self.subTest(test=t.name, case=case.get("name"), series=s.get("series")):
                        self.assertIsInstance(s.get("values"), str, "quote it: values: '0x90'")


if __name__ == "__main__":
    unittest.main()
