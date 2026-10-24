"""The schema catalog must be pinned, and a skipped kind must be explained by measurement.

⛔ Why this test exists. On 2026-10-08 the datree CRDs-catalog regenerated
external-secrets.io/clustersecretstore_v1.json (upstream PR #988) with a generator defect
-- `"additionalProperties": false` leaked into a `properties` map as if it were a property
name. kubeconform cannot use such a schema and reports it as `statusSkipped` with an empty
msg, which is byte-identical to a kind that genuinely has no schema. The gate then printed
"kinds with NO SCHEMA ... (coverage gap, not a pass)" and went red on EVERY merge request
in this repo, for hours, for a third party's edit -- and the honest remedy (roll the pinned
ref back) was not among the printed options.

Two invariants fall out of that, and both are asserted here:

  1. The catalog is addressed by COMMIT, never by `main`. Then a bad upstream revision can
     only reach us as a deliberate, reviewable bump, and rolling back is one line.
  2. A skip is classified by ASKING the catalog (404 vs 200 vs fetch error), never assumed
     to mean "missing". The three verdicts have different remedies and only one of them is
     ours to fix in validation-exceptions.yaml.

The classification tests inject a fake fetch, so they never touch the network.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_validator():
    """tools/ci/validate-manifests.py has a hyphen in its name, so it is not importable."""
    spec = importlib.util.spec_from_file_location(
        "validate_manifests", ROOT / "tools" / "ci" / "validate-manifests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load_validator()


class CatalogIsPinned(unittest.TestCase):
    def test_ref_is_a_commit_not_a_branch(self):
        self.assertNotEqual(v.CATALOG_REF, "main")
        self.assertRegex(v.CATALOG_REF, r"^[0-9a-f]{40}$",
                         "a branch or tag can be moved under us; pin a full commit SHA")

    def test_no_branch_name_survives_anywhere_in_the_url(self):
        # The template is what kubeconform is handed, so the pin has to be visible there.
        self.assertIn("/" + v.CATALOG_REF + "/", v.CRD_CATALOG)
        self.assertNotIn("/main/", v.CRD_CATALOG)
        # ...and kubeconform's placeholders must be intact (an f-string here would eat
        # the doubled braces down to a single one and silently break every lookup).
        for placeholder in ("{{.Group}}", "{{.ResourceKind}}", "{{.ResourceAPIVersion}}"):
            self.assertIn(placeholder, v.CRD_CATALOG)


class CatalogUrlMatchesWhatKubeconformAsksFor(unittest.TestCase):
    """Verified against kubeconform v0.7.0's own access log: the KIND is lowercased."""

    def test_grouped_resource(self):
        self.assertEqual(
            v.catalog_url("external-secrets.io/v1", "ClusterSecretStore"),
            f"{v.CATALOG_REPO}/{v.CATALOG_REF}"
            "/external-secrets.io/clustersecretstore_v1.json")

    def test_version_keeps_its_group_prefix_out_of_the_filename(self):
        url = v.catalog_url("postgresql.cnpg.io/v1", "Cluster")
        self.assertTrue(url.endswith("/postgresql.cnpg.io/cluster_v1.json"), url)

    def test_core_group_is_not_double_slashed(self):
        # apiVersion "v1" has no group: the template yields an empty {{.Group}}, and the
        # double slash is what kubeconform actually requests. Do not "tidy" it away.
        self.assertTrue(v.catalog_url("v1", "Pod").endswith("//pod_v1.json"))


class SkipsAreClassifiedByMeasurement(unittest.TestCase):
    def classify(self, fetch):
        return v.classify_schema("https://example.invalid/x.json", fetch=fetch)

    def test_404_is_a_real_coverage_gap(self):
        verdict, _ = self.classify(lambda url: (404, b""))
        self.assertEqual(verdict, v.SCHEMA_MISSING)

    def test_200_that_parses_is_an_upstream_defect_not_a_gap(self):
        # The 2026-10-08 case: the file is there and well-formed, kubeconform still skipped.
        verdict, detail = self.classify(lambda url: (200, b'{"type": "object"}'))
        self.assertEqual(verdict, v.SCHEMA_UNUSABLE)
        self.assertIn("B", detail)  # names the size, so the log shows it is not an empty body

    def test_200_that_does_not_parse_is_also_unusable(self):
        verdict, _ = self.classify(lambda url: (200, b"<html>not json</html>"))
        self.assertEqual(verdict, v.SCHEMA_UNUSABLE)

    def test_other_http_statuses_are_availability_not_ours(self):
        for status in (403, 429, 500, 502):
            with self.subTest(status=status):
                verdict, detail = self.classify(lambda url, s=status: (s, b""))
                self.assertEqual(verdict, v.SCHEMA_UNFETCHABLE)
                self.assertIn(str(status), detail)

    def test_a_network_fault_is_unfetchable_not_missing(self):
        def boom(url):
            raise OSError("connection refused")
        verdict, detail = self.classify(boom)
        self.assertEqual(verdict, v.SCHEMA_UNFETCHABLE)
        self.assertIn("connection refused", detail)

    def test_each_verdict_is_distinct_and_reported(self):
        # ⛔ Empty selection is not a pass: a bug that collapsed the three verdicts into
        # one would still have to fail this.
        self.assertEqual(len({v.SCHEMA_MISSING, v.SCHEMA_UNUSABLE, v.SCHEMA_UNFETCHABLE}), 3)

    def test_results_are_cached_so_a_kind_is_fetched_once(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 404, b""
        cache = {}
        for _ in range(3):
            v.classify_schema("https://example.invalid/x.json", cache=cache, fetch=fetch)
        self.assertEqual(len(calls), 1)


class MissingSchemaIsStillAFailure(unittest.TestCase):
    """The pin and the classifier must not soften the gate: a real gap still fails."""

    def test_a_missing_schema_returns_a_non_zero_rc(self):
        skipped = {"resources": [{"status": "statusSkipped", "kind": "Widget",
                                  "version": "example.com/v1"}], "summary": {}}
        original = v.classify_schema
        v.classify_schema = lambda url, cache=None, fetch=None: (v.SCHEMA_MISSING, "")
        try:
            self.assertEqual(v.report("t", skipped, set(), [], {}), 1)
        finally:
            v.classify_schema = original

    def test_an_unusable_schema_also_fails_but_is_labelled_upstream(self):
        skipped = {"resources": [{"status": "statusSkipped", "kind": "Widget",
                                  "version": "example.com/v1"}], "summary": {}}
        original = v.classify_schema
        v.classify_schema = lambda url, cache=None, fetch=None: (v.SCHEMA_UNUSABLE, "1 B")
        try:
            self.assertEqual(v.report("t", skipped, set(), [], {}), 1)
        finally:
            v.classify_schema = original


class RealCatalogStillValidates(unittest.TestCase):
    """A pin that points at a ref where the schemas are broken is worse than no pin."""

    def test_the_pinned_ref_serves_a_usable_eso_schema(self):
        try:
            status, body = v.http_fetch(v.catalog_url("external-secrets.io/v1",
                                                      "ClusterSecretStore"), attempts=1)
        except OSError as e:
            self.skipTest(f"catalog unreachable from here: {e}")
        self.assertEqual(status, 200)
        # The defect is structural, so assert on the shape rather than on the byte count:
        # a property literally named "additionalProperties" must not be present.
        import json as _json
        doc = _json.loads(body)
        leaked = []

        def scan(node, path=""):
            if isinstance(node, dict):
                props = node.get("properties")
                if isinstance(props, dict) and "additionalProperties" in props:
                    leaked.append(path)
                for k, val in node.items():
                    scan(val, f"{path}/{k}")
            elif isinstance(node, list):
                for i, val in enumerate(node):
                    scan(val, f"{path}[{i}]")
        scan(doc)
        self.assertEqual(
            leaked, [],
            "the pinned catalog ref leaks `additionalProperties` into a `properties` map, "
            "which makes kubeconform skip the kind -- bump CRD_CATALOG_REF to a ref that "
            "does not, or add an entry to validation-exceptions.yaml with a reason")


if __name__ == "__main__":
    unittest.main()
