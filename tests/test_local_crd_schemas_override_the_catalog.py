"""A CRD whose catalog schema is older than the cluster's CRD is validated by a LOCAL schema.

Why this test exists. fleet !2221 (2026-11-11, infra-r9mzd.54) enabled
`spec.restic.exclude.caches` on jump/jump-home-ceph. The cluster's VolSync 0.17 CRD has the
field and `kubectl apply --dry-run=server` accepted it, but fleet-validate failed:
`additional properties 'exclude' not allowed`. The datree catalog's volsync schema was last
regenerated 2025-04-06 (#529), so even its `main` lacks the field, and no CATALOG_REF bump
can fix it. A third-party catalog lagging behind the CRDs we actually run is a recurring
shape, so the remedy is structural: tools/ci/schemas/ holds schemas generated from the
cluster's own CRDs, and kubeconform consults it BEFORE the catalog.

Asserted here:
  1. the local location is searched before the catalog (order is the whole point);
  2. every local schema is strict (additionalProperties false on spec), so overriding
     the catalog never weakens validation;
  3. behaviour: the real jump-home RS validates against the local schema, and a typo'd
     field under restic is still rejected.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "tools" / "ci" / "schemas"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_manifests", ROOT / "tools" / "ci" / "validate-manifests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load_validator()


def kubeconform() -> str | None:
    return shutil.which("kubeconform") or next(
        (str(p) for p in [ROOT / ".bin" / "kubeconform"] if p.exists()), None)


class LocalSchemasComeFirst(unittest.TestCase):
    def test_local_location_precedes_the_catalog(self):
        args = v.kubeconform_args([], "1.33.0", "kubeconform")
        locs = [args[i + 1] for i, a in enumerate(args) if a == "-schema-location"]
        self.assertIn(v.LOCAL_SCHEMAS, locs)
        self.assertLess(locs.index(v.LOCAL_SCHEMAS), locs.index(v.CRD_CATALOG))
        self.assertTrue(v.LOCAL_SCHEMAS.startswith(str(SCHEMAS)))
        for placeholder in ("{{.Group}}", "{{.ResourceKind}}", "{{.ResourceAPIVersion}}"):
            self.assertIn(placeholder, v.LOCAL_SCHEMAS)

    def test_every_local_schema_is_strict(self):
        files = sorted(SCHEMAS.rglob("*.json"))
        self.assertTrue(files, "no local schemas -- the override directory is empty")
        for f in files:
            spec = json.loads(f.read_text())["properties"]["spec"]
            self.assertIs(spec.get("additionalProperties"), False, f"{f} is not strict")


class JumpHomeReplicationSourceValidates(unittest.TestCase):
    def setUp(self):
        self.kc = kubeconform()
        if not self.kc:
            self.fail("kubeconform not found: this behaviour check must run, not skip")
        self.tmp = Path(tempfile.mkdtemp(prefix="local-schema-test."))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def validate(self, doc: dict) -> list[dict]:
        p = self.tmp / "rs.yaml"
        p.write_text(yaml.safe_dump(doc))
        out = v.run_kubeconform([str(p)], "1.33.0", self.kc)
        return [r for r in out.get("resources", []) if r.get("status") != "statusValid"]

    def real_rs(self) -> dict:
        docs = yaml.safe_load_all((ROOT / "jump" / "volsync-backup.yml").read_text())
        return next(d for d in docs if d and d.get("kind") == "ReplicationSource")

    def test_the_real_rs_is_valid(self):
        self.assertEqual(self.validate(self.real_rs()), [])

    def test_jump_home_enables_exclude_caches(self):
        # The live tags from ~/bin/home_tidy.py only take effect with this on.
        self.assertIs(self.real_rs()["spec"]["restic"]["exclude"]["caches"], True)

    def test_restic_exclude_caches_is_valid(self):
        rs = self.real_rs()
        rs["spec"]["restic"]["exclude"] = {"caches": True}
        self.assertEqual(self.validate(rs), [])

    def test_a_typo_under_restic_is_still_rejected(self):
        rs = self.real_rs()
        rs["spec"]["restic"]["exlcude"] = {"caches": True}
        bad = self.validate(rs)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["status"], "statusInvalid")


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "crd_to_schema", ROOT / "tools" / "ci" / "crd-to-schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


g = load_generator()


class GeneratorMirrorsTheCatalog(unittest.TestCase):
    def test_objects_with_properties_are_closed(self):
        out = g.strict({"type": "object", "properties": {"a": {"type": "object",
                        "properties": {"b": {"type": "string"}}}}})
        self.assertIs(out["additionalProperties"], False)
        self.assertIs(out["properties"]["a"]["additionalProperties"], False)

    def test_free_form_maps_stay_open(self):
        labels = {"type": "object", "additionalProperties": {"type": "string"}}
        self.assertEqual(g.strict(labels), labels)
        self.assertNotIn("additionalProperties", g.strict({"type": "object"}))

    def test_int_or_string_accepts_both(self):
        out = g.strict({"type": "string", "x-kubernetes-int-or-string": True})
        self.assertEqual(out, {"oneOf": [{"type": "string"}, {"type": "integer"}]})

    def test_arrays_are_walked(self):
        out = g.strict({"anyOf": [{"properties": {"x": {}}}]})
        self.assertIs(out["anyOf"][0]["additionalProperties"], False)

    def test_one_file_per_served_version_named_for_kubeconform(self):
        crd = {"spec": {"group": "g.io", "names": {"kind": "Thing"}, "versions": [
            {"name": "v1", "served": True, "schema": {"openAPIV3Schema": {}}},
            {"name": "v0", "served": False, "schema": {"openAPIV3Schema": {}}}]}}
        self.assertEqual(list(g.schemas(crd)), ["g.io/thing_v1.json"])

    def test_committed_volsync_schema_is_what_the_generator_writes(self):
        # Regenerating from the same CRD must be a no-op diff: the file is not hand-edited.
        f = SCHEMAS / "volsync.backube" / "replicationsource_v1alpha1.json"
        schema = json.loads(f.read_text())
        self.assertEqual(g.strict(schema), schema)


if __name__ == "__main__":
    unittest.main()
