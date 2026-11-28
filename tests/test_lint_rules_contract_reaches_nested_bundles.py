"""lint.py's alert-rule contract must reach rule files in NESTED sub-bundles.

monitoring-rules/ outgrew Fleet's helm release ceiling on 2026-11-19 (the release
Secret may hold at most 1,048,576 bytes; v712 was 1,047,252 and v713 was refused,
infra-3rm.33), so part of it moved into a nested sub-bundle,
monitoring-rules/batch-jobs/. The contract loop used to glob `*.yml` in the top
directory only: a file one level down would merge with a black-holed `team`, a
missing `monitoring-stack` selector or an off-taxonomy component, green.

The test runs the real lint.py against a throwaway tree that holds one broken rule
in a nested directory, and asserts lint names that file.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent

BROKEN_RULE = textwrap.dedent("""\
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    metadata:
      name: nested-probe
      labels:
        monitoring-stack: mdapi
    spec:
      groups:
      - name: nested
        rules:
        - alert: NestedAlertWithoutTeam
          expr: vector(1)
          labels:
            severity: warning
    """)


class LintReachesNestedRuleBundles(unittest.TestCase):
    def test_broken_rule_in_nested_bundle_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shutil.copy(REPO / "lint.py", root / "lint.py")
            ## lint derives the component taxonomy from this file and stops
            ## without it, before the rule loop ever runs.
            adapter = pathlib.Path("monitoring-statuspage-adapter", "adapter-cm.yml")
            (root / adapter.parent).mkdir()
            shutil.copy(REPO / adapter, root / adapter)
            nested = root / "monitoring-rules" / "batch-jobs"
            nested.mkdir(parents=True)
            (nested / "12-nested.yml").write_text(BROKEN_RULE)
            out = subprocess.run([sys.executable, str(root / "lint.py")],
                                 capture_output=True, text=True, cwd=root)
            text = out.stdout + out.stderr
            self.assertNotEqual(out.returncode, 0, text)
            self.assertIn("[RULES-TEAM] monitoring-rules/batch-jobs/12-nested.yml", text)


if __name__ == "__main__":
    unittest.main()
