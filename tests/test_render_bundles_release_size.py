"""tools/ci/render-bundles.py sizes each CHART bundle's helm release (infra-3rm.35).

The raw-bundle invariant test cannot see a chart bundle's release: Helm stores the
chart's templates and the render, neither of which is in this repo for a remote
chart. kagent-crds (OCI chart) stood at 855,160 B = 82% of the 1 MiB Secret limit on
2026-11-20 with no check on it. render-bundles.py already renders every chart bundle
in CI; it now also pulls the chart and estimates the release.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).absolute().parent.parent / "tools/ci/render-bundles.py"
_spec = importlib.util.spec_from_file_location("render_bundles", _PATH)
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)


def done(rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, "", stderr)


class ChartRefArgs(unittest.TestCase):
    def test_local_chart_is_its_resolved_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "charts" / "x").mkdir(parents=True)
            self.assertEqual(rb.chart_ref_args(d, {"chart": "./charts/x", "repo": "r", "version": "1"}),
                             [str((d / "charts" / "x").resolve()), "--version", "1"])

    def test_remote_chart_with_repo_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rb.chart_ref_args(Path(tmp), {"chart": "c", "repo": "https://r", "version": "2"}),
                             ["c", "--repo", "https://r", "--version", "2"])

    def test_oci_chart_without_repo_or_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rb.chart_ref_args(Path(tmp), {"chart": "oci://g/c"}), ["oci://g/c"])


class ChartDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / "bundle"
        self.d.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.calls = []
        self._sh = rb.sh

    def tearDown(self):
        rb.sh = self._sh
        self.tmp.cleanup()

    def fake_pull(self, rc=0, make=("kagent-crds",)):
        def sh(args, cwd=None, env=None):
            self.calls.append(args)
            dest = Path(args[args.index("-d") + 1])
            for name in make:
                (dest / name / "templates").mkdir(parents=True)
            return done(rc, "pull: denied" if rc else "")
        rb.sh = sh

    def test_local_chart_needs_no_pull(self):
        (self.d / "chart").mkdir()
        got, errs = rb.chart_dir(self.root, self.d, {"helm": {"chart": "./chart"}}, self.scratch)
        self.assertEqual((got, errs, self.calls), ((self.d / "chart").resolve(), [], []))

    def test_remote_chart_is_pulled_untarred_with_the_same_ref(self):
        self.fake_pull()
        spec = {"helm": {"chart": "oci://ghcr.io/x/kagent-crds", "version": "0.10.6"}}
        got, errs = rb.chart_dir(self.root, self.d, spec, self.scratch)
        self.assertEqual(errs, [])
        self.assertEqual(got.name, "kagent-crds")
        args = self.calls[0]
        self.assertEqual(args[1:3], ["pull", "oci://ghcr.io/x/kagent-crds"])
        self.assertIn("--untar", args)
        self.assertEqual(args[args.index("--version") + 1], "0.10.6")

    def test_remote_chart_named_like_a_directory_in_cwd_is_still_pulled(self):
        # CI runs from the repo root, where e.g. oauth2-proxy/ is a bundle directory.
        (self.root / "oauth2-proxy").mkdir()
        self.fake_pull(make=("oauth2-proxy",))
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            got, errs = rb.chart_dir(self.root, self.d, {"helm": {"chart": "oauth2-proxy",
                                                                  "repo": "https://r"}}, self.scratch)
        finally:
            os.chdir(cwd)
        self.assertEqual(errs, [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(got.parent.name, "pull-bundle")

    def test_failed_pull_reports_the_last_stderr_line(self):
        def sh(args, cwd=None, env=None):
            return done(1, "Pulling...\nError: manifest unknown\n")
        rb.sh = sh
        _, errs = rb.chart_dir(self.root, self.d, {"helm": {"chart": "c"}}, self.scratch)
        self.assertEqual(errs, ["bundle: helm pull failed: Error: manifest unknown"])

    def test_failed_pull_is_an_error_never_a_skip(self):
        self.fake_pull(rc=1)
        got, errs = rb.chart_dir(self.root, self.d, {"helm": {"chart": "c"}}, self.scratch)
        self.assertIsNone(got)
        self.assertEqual(errs, ["bundle: helm pull failed: pull: denied"])

    def test_pull_that_yields_no_single_chart_is_an_error(self):
        self.fake_pull(make=("a", "b"))
        got, errs = rb.chart_dir(self.root, self.d, {"helm": {"chart": "c"}}, self.scratch)
        self.assertIsNone(got)
        self.assertEqual(len(errs), 1)
        self.assertIn("expected one chart", errs[0])


class ReleaseSize(unittest.TestCase):
    def test_estimate_uses_templates_and_the_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "b"
            (d / "chart" / "templates").mkdir(parents=True)
            (d / "chart" / "templates" / "t.yaml").write_text("k: v\n")
            spec = {"helm": {"chart": "./chart"}}
            small, errs = rb.release_size(root, d, spec, "k: v\n", root)
            big, _ = rb.release_size(root, d, spec, "k: v\n" + os.urandom(20000).hex(), root)
            self.assertEqual(errs, [])
            self.assertGreater(big, small + 20000)

    def test_chart_dir_error_propagates(self):
        _sh = rb.sh
        rb.sh = lambda args, cwd=None, env=None: done(1, "boom")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                size, errs = rb.release_size(Path(tmp), Path(tmp), {"helm": {"chart": "c"}}, "", Path(tmp))
        finally:
            rb.sh = _sh
        self.assertIsNone(size)
        self.assertEqual(len(errs), 1)


class SuppliedValues(unittest.TestCase):
    def test_values_files_then_inline_values_missing_files_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "v.yaml").write_bytes(b"a: 1\n")
            spec = {"helm": {"valuesFiles": ["v.yaml", "gone.yaml"], "values": {"b": 2}}}
            self.assertEqual(rb.supplied_values(d, spec), b"a: 1\n\nb: 2\n")

    def test_no_values_is_empty(self):
        self.assertEqual(rb.supplied_values(Path("/nonexistent"), {"helm": {}}), b"")
        self.assertEqual(rb.supplied_values(Path("/nonexistent"), {}), b"")


if __name__ == "__main__":
    unittest.main()
