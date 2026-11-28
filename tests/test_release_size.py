"""tools/ci/release_size.py: the helm release Secret estimator (infra-3rm.33/.35).

A Fleet bundle is stored as a Helm release, one Secret per revision, refused above
1,048,576 bytes. Helm stores base64(gzip(JSON)) holding the TOP chart (subcharts only
through the manifest), the supplied values and the rendered manifest. For a
raw-manifest bundle the bundle files are the chart.

Calibration, 2026-11-20 (current estimate() vs the live deployed release Secret):
  mdapi-prod-monitoring-rules (raw)            743,152 vs 739,756  (+0.5%)
  mdapi-prod-monitoring-rules-batch-jobs (raw) 313,912 vs 314,796  (-0.3%)
  kagent-crds (remote OCI chart, 2 subcharts)  858,476 vs 855,160  (+0.4%)
  gitlab (vendored chart + kustomize)          232,448 vs 213,824  (+8.7%)
The 10% headroom under the gate absorbs the small under-estimate on raw bundles.
Over-estimates err on the safe side; re-run this comparison before changing estimate().
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).absolute().parent.parent / "tools/ci/release_size.py"
_spec = importlib.util.spec_from_file_location("release_size", _PATH)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


class Estimate(unittest.TestCase):
    def test_raw_bundle_counts_content_about_twice(self):
        body = os.urandom(30000).hex().encode()  # 60 KB of incompressible-ish text
        self.assertGreater(rs.estimate({"a.yml": b"k: " + body}), 60000 * 4 // 3 * 1.2)

    def test_marker_contributes_nothing(self):
        files = {"a.yml": b"k: v\n"}
        self.assertEqual(rs.estimate(files), rs.estimate({**files, "fleet.yaml": b"x: " + b"y" * 5000}))

    def test_non_yaml_file_is_a_chart_file_but_not_manifest(self):
        self.assertGreater(rs.estimate({"a.yml": b"k: v\n", "b.json": os.urandom(4000)}),
                           rs.estimate({"a.yml": b"k: v\n"}))

    def test_unparseable_yaml_still_counts_as_a_file(self):
        self.assertGreater(rs.estimate({"bad.yml": b"a: [\n" + os.urandom(2000).hex().encode()}),
                           rs.estimate({}))

    def test_given_manifest_replaces_the_derived_one(self):
        big = os.urandom(20000).hex()
        self.assertGreater(rs.estimate({"t.yaml": b"k: v\n"}, manifest=big),
                           rs.estimate({"t.yaml": b"k: v\n"}) + 20000)

    def test_result_is_base64_length(self):
        self.assertEqual(rs.estimate({"a.yml": b"k: v\n"}) % 4, 0)


class ChartFiles(unittest.TestCase):
    def test_the_top_chart_is_stored_and_subcharts_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp)
            for rel in ("templates/a.yaml", "templates/sub/b.tpl", "Chart.yaml", "values.yaml",
                        "crds/x.yaml", "charts/sub/templates/c.yaml", "charts/sub/values.yaml"):
                (c / rel).parent.mkdir(parents=True, exist_ok=True)
                (c / rel).write_bytes(rel.encode())
            self.assertEqual(sorted(rs.chart_files(c)),
                             ["Chart.yaml", "crds/x.yaml", "templates/a.yaml",
                              "templates/sub/b.tpl", "values.yaml"])
            self.assertEqual(rs.chart_files(c)["values.yaml"], b"values.yaml")

    def test_empty_chart_dir_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rs.chart_files(Path(tmp)), {})


class Oversized(unittest.TestCase):
    def test_names_only_bundles_at_or_over_the_fail_ratio(self):
        at = int(rs.SECRET_DATA_LIMIT * rs.FAIL_RATIO)
        got = rs.oversized({"big": at, "small": at - 1})
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("big/:"), got)

    def test_the_incident_size_fails_with_an_actionable_message(self):
        self.assertEqual(rs.oversized({"monitoring-rules": 1047252}), [
            "monitoring-rules/: estimated release Secret 1,047,252 B = 99.9% of the 1 MiB "
            "limit (fails at 90%). Split the bundle (see monitoring-rules/batch-jobs/"
            "fleet.yaml), or for an upstream chart drop what it does not need to ship."])

    def test_kagent_crds_today_passes_with_margin_logged(self):
        # 82% live on 2026-11-20: below the gate, so CI stays green until it grows.
        self.assertEqual(rs.oversized({"kagent-crds": 857444}), [])

    def test_constants(self):
        self.assertEqual(rs.SECRET_DATA_LIMIT, 1048576)
        self.assertEqual(rs.FAIL_RATIO, 0.90)


if __name__ == "__main__":
    unittest.main()
