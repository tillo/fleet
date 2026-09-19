#!/usr/bin/env python3
"""Schema-validate every Kubernetes manifest in this repo, rendered and raw.

Two inputs:
  * the rendered output of tools/ci/render-bundles.py (the 31 chart bundles)
  * every tracked YAML file that is actually a Kubernetes manifest

"Actually a manifest" is decided by content, not by a hand-maintained exclude list: a
file qualifies only if EVERY document in it carries apiVersion+kind. That drops values
files, .gitlab-ci.yml and lint config automatically, so the filter cannot rot.

Two things ARE excluded by path, because they are inputs rather than manifests:
  * fleet.yaml / fleet.yml -- Fleet's own bundle config (⛔ BOTH spellings; this repo
    uses .yml for 32 of its 153 bundles and an `*.yaml`-only sweep silently skips them)
  * anything under a bundle's kustomize dir -- strategic-merge patch fragments do carry
    apiVersion+kind but are partial by design, so validating them fails on fields the
    patch deliberately omits.

⛔ Never publish the rendered directory as a CI artifact. Three charts (horizon,
victoria-metrics-operator, vpa) generate a fresh self-signed CA and private key on every
render, so the output contains real private keys -- verified 2026-09-12 by rendering
twice and diffing.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

BUNDLE_MARKERS = ("fleet.yaml", "fleet.yml")

# kubeconform ships no schema for CustomResourceDefinition itself and skips it by
# design. Everything else that skips is a genuine coverage gap and fails the job unless
# it is listed, with a reason, in validation-exceptions.yaml.
ALLOWED_SKIPPED_KINDS = {"CustomResourceDefinition"}

EXCEPTIONS_FILE = Path(__file__).with_name("validation-exceptions.yaml")

CRD_CATALOG = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


def repo_root() -> Path:
    return Path(sh(["git", "rev-parse", "--show-toplevel"]).stdout.strip())


def tracked_manifests(root: Path) -> tuple[list[str], collections.Counter]:
    files = [f for f in sh(["git", "ls-files", "-z"], cwd=root).stdout.split("\0") if f]

    kustomize_dirs: set[str] = set()
    values_files: set[str] = set()
    for f in files:
        base = os.path.basename(f)
        if base in BUNDLE_MARKERS:
            d = os.path.dirname(f)
            try:
                spec = yaml.safe_load((root / f).read_text()) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(spec, dict):
                continue
            kd = (spec.get("kustomize") or {}).get("dir")
            if kd:
                kustomize_dirs.add(os.path.normpath(os.path.join(d, kd)))
            for vf in (spec.get("helm") or {}).get("valuesFiles") or []:
                values_files.add(os.path.normpath(os.path.join(d, vf)))
        # Fleet also auto-detects a bare kustomization.yaml, with no fleet.yaml mention
        if base.lower() in ("kustomization.yaml", "kustomization.yml"):
            kustomize_dirs.add(os.path.dirname(f))

    manifests: list[str] = []
    skipped: collections.Counter = collections.Counter()
    for f in files:
        if not f.endswith((".yaml", ".yml")):
            continue
        if os.path.basename(f) in BUNDLE_MARKERS:
            skipped["fleet bundle config"] += 1
            continue
        if f in values_files:
            skipped["helm valuesFiles"] += 1
            continue
        if any(f == d or f.startswith(d + "/") for d in kustomize_dirs):
            skipped["kustomize input"] += 1
            continue
        try:
            docs = list(yaml.safe_load_all((root / f).read_text()))
        except yaml.YAMLError:
            skipped["unparseable YAML"] += 1
            continue
        real = [d for d in docs if isinstance(d, dict) and d]
        if not real:
            skipped["empty"] += 1
            continue
        if not all(d.get("apiVersion") and d.get("kind") for d in real):
            skipped["not a k8s manifest"] += 1
            continue
        manifests.append(str(root / f))
    return manifests, skipped


def run_kubeconform(targets: list[str], kube_version: str, kubeconform: str) -> dict:
    args = [
        kubeconform,
        "-strict",
        "-kubernetes-version", kube_version,
        "-schema-location", "default",
        "-schema-location", CRD_CATALOG,
        "-ignore-missing-schemas",   # skips are inspected below, not waved through
        "-verbose",
        "-output", "json",
        "-summary",
    ] + targets
    res = subprocess.run(args, text=True, capture_output=True)
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        sys.exit(f"kubeconform produced no parseable output:\n{res.stdout[:2000]}\n{res.stderr[:2000]}")


def load_exceptions() -> tuple[set[tuple[str, str]], list[dict]]:
    if not EXCEPTIONS_FILE.exists():
        return set(), []
    doc = yaml.safe_load(EXCEPTIONS_FILE.read_text()) or {}
    schemas = {
        (e["apiVersion"], e["kind"])
        for e in doc.get("allowedMissingSchemas") or []
    }
    return schemas, list(doc.get("allowedFindings") or [])


def finding_allowed(r: dict, allowed: list[dict]) -> dict | None:
    for a in allowed:
        if (os.path.basename(r.get("filename", "")) == a.get("file")
                and r.get("kind") == a.get("kind")
                and r.get("name") == a.get("name")
                and a.get("messageContains", "") in str(r.get("msg"))):
            return a
    return None


def report(label: str, data: dict, allowed_schemas: set, allowed_findings: list) -> int:
    summary = data.get("summary", {})
    bad = [r for r in data.get("resources", []) if r["status"] in ("statusInvalid", "statusError")]
    print(f"\n{label}: {summary}")

    rc = 0
    unexpected: collections.Counter = collections.Counter()
    excepted: collections.Counter = collections.Counter()
    for r in data.get("resources", []):
        if r["status"] != "statusSkipped":
            continue
        kind = r.get("kind", "?")
        key = (r.get("version", "?"), kind)
        if kind in ALLOWED_SKIPPED_KINDS:
            continue
        # ⚠️ match on apiVersion+kind: `ClusterIssuer` and `NetworkPolicy` each exist in
        # two groups here, one covered by a schema and one not.
        (excepted if key in allowed_schemas else unexpected)[key] += 1

    if excepted:
        print("  unvalidated by documented exception (see validation-exceptions.yaml):")
        for (v, k), n in sorted(excepted.items()):
            print(f"    {n:4d}  {v} {k}")
    if unexpected:
        print("  kinds with NO SCHEMA and NO exception (coverage gap, not a pass):")
        for (v, k), n in sorted(unexpected.items(), key=lambda x: -x[1]):
            print(f"    {n:4d}  {v} {k}")
        rc = 1

    for r in bad:
        # print identities and constraint paths, never values -- rendered manifests
        # contain chart-generated Secrets
        where = os.path.basename(r.get("filename", "?"))
        allowed = finding_allowed(r, allowed_findings)
        prefix = "KNOWN" if allowed else "FAIL"
        print(f"  {prefix} {r.get('kind')}/{r.get('name')} in {where}: {str(r.get('msg'))[:300]}")
        if not allowed:
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rendered", help="output directory from render-bundles.py")
    ap.add_argument("--kube-version", default="1.35.2")
    ap.add_argument("--kubeconform", default=os.environ.get("KUBECONFORM", "kubeconform"))
    args = ap.parse_args()

    root = repo_root()
    rc = 0
    allowed_schemas, allowed_findings = load_exceptions()

    manifests, skipped = tracked_manifests(root)
    print(f"tracked manifest files: {len(manifests)}")
    print(f"not validated (by design): {dict(skipped)}")
    rc |= report("tracked manifests", run_kubeconform(manifests, args.kube_version, args.kubeconform),
                 allowed_schemas, allowed_findings)

    if args.rendered:
        rendered = Path(args.rendered)
        files = sorted(str(p) for p in rendered.glob("*.yaml"))
        if not files:
            print(f"\nno rendered manifests found in {rendered} -- did render-bundles.py run?")
            return 1
        print(f"\nrendered bundle files: {len(files)}")
        rc |= report("rendered bundles", run_kubeconform(files, args.kube_version, args.kubeconform),
                     allowed_schemas, allowed_findings)

    print("\nValidation FAILED." if rc else "\nValidation passed.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
