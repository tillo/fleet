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

⛔ The datree schema catalog is PINNED to a commit, never `main` (see CATALOG_REF), and a
skip is CLASSIFIED by asking the catalog itself rather than assumed to mean "no schema"
(see classify_schema). Both exist because of 2026-10-08: catalog PR #988 rewrote
external-secrets.io/clustersecretstore_v1.json and the new revision leaks
`"additionalProperties": false` into a `properties` map as if it were a property name.
kubeconform cannot use such a schema and reports it as statusSkipped with an empty msg --
byte-identical to a kind that genuinely has no schema. The gate therefore called an
upstream defect a "coverage gap" and went red on every MR in this repo for hours, while
the honest remedy (roll the catalog ref back) was not among the printed options.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BUNDLE_MARKERS = ("fleet.yaml", "fleet.yml")

# A directory holding one of these is a VENDORED UPSTREAM TREE (gitlab/charts/ is the
# first — infra-h0xb). Its YAML is Helm templates and chart metadata, not manifests:
# 209 files that would otherwise land in the "unparseable YAML" bucket and drown the
# real signal. lint.py carries the full rationale for the marker.
VENDORED_MARKER = ".vendored"

# kubeconform ships no schema for CustomResourceDefinition itself and skips it by
# design. Everything else that skips is a genuine coverage gap and fails the job unless
# it is listed, with a reason, in validation-exceptions.yaml.
ALLOWED_SKIPPED_KINDS = {"CustomResourceDefinition"}

EXCEPTIONS_FILE = Path(__file__).with_name("validation-exceptions.yaml")

# ⛔ PINNED, never `main`. The catalog is regenerated continuously by a bot, and one
# regeneration can land a file kubeconform cannot use -- which this gate then reports as
# "no schema" and fails EVERY MR in this repo for a third party's edit (2026-10-08,
# catalog PR #988; the module docstring has the full story). Pinning makes the gate a
# function of what we committed: a catalog bump is now a reviewable commit here, and a bad
# bump is a one-line revert. CRD_CATALOG_REF overrides it for a trial bump.
CATALOG_REPO = "https://raw.githubusercontent.com/datreeio/CRDs-catalog"
CATALOG_REF = os.environ.get(
    "CRD_CATALOG_REF",
    # parent of b7e2015f4fc9 ("update external-secrets.io CRDs (#988)")
    "f1e7f6bc0537bf0622ffe6e47dbaa85914fabbec",
)
# Plain concatenation, NOT an f-string: the template's `{{.Group}}` braces are
# kubeconform's, and an f-string would eat them down to a single brace.
CRD_CATALOG = (
    CATALOG_REPO + "/" + CATALOG_REF + "/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)

# Why kubeconform skipped a kind. It cannot tell us, so we ask the catalog (classify_schema).
SCHEMA_MISSING = "missing"          # 404 -- genuinely no schema published
SCHEMA_UNUSABLE = "unusable"        # 200 -- the schema is there, kubeconform cannot use it
SCHEMA_UNFETCHABLE = "unfetchable"  # anything else -- availability, not our manifests


def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


def repo_root() -> Path:
    return Path(sh(["git", "rev-parse", "--show-toplevel"]).stdout.strip())


def tracked_manifests(root: Path) -> tuple[list[str], collections.Counter]:
    files = [f for f in sh(["git", "ls-files", "-z"], cwd=root).stdout.split("\0") if f]

    # Vendored upstream trees are inputs, like a chart's values file: not ours to
    # schema-check. (`git ls-files`, so the marker itself must be tracked.)
    vendored = tuple(sorted({
        os.path.dirname(f) for f in files if os.path.basename(f) == VENDORED_MARKER
    }))

    def is_vendored(f: str) -> bool:
        return any(f.startswith(d + "/") for d in vendored)

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
        if is_vendored(f):
            skipped["vendored upstream chart"] += 1
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


def catalog_url(version: str, kind: str) -> str:
    """The URL kubeconform's -schema-location template expands to for one kind.

    Verified against kubeconform v0.7.0 by pointing the template at a local HTTP server
    and reading its access log: `{{.ResourceKind}}` IS lowercased, `{{.Group}}` and the
    rest are literal. So this reconstructs exactly what the validator asked for.
    """
    group, slash, api_version = version.partition("/")
    if not slash:
        # Core group: apiVersion is a bare "v1", and `partition` puts the WHOLE string in
        # `group`. {{.Group}} is empty for these, so the real request is `.../ref//pod_v1.json`
        # -- double slash included. (In practice the built-in schemas answer first, so this
        # path is only reached for a core kind with no embedded schema.)
        group, api_version = "", version
    return f"{CATALOG_REPO}/{CATALOG_REF}/{group}/{kind.lower()}_{api_version}.json"


def http_fetch(url: str, attempts: int = 3, timeout: int = 20) -> tuple[int, bytes]:
    """GET url -> (status, body). A 404 is final; everything else is retried with backoff."""
    last: OSError | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            last = e
        except OSError as e:  # HTTPError is an OSError; both land here on a retryable fault
            last = e
        if i + 1 < attempts:
            time.sleep(2 ** i)
    raise last if last is not None else OSError(f"could not fetch {url}")


def classify_schema(url: str, cache: dict | None = None, fetch=http_fetch):
    """Why did kubeconform skip this kind? Returns (verdict, detail).

    kubeconform reports BOTH "no schema published" and "schema exists but I cannot use it"
    as `statusSkipped` with an empty msg, so its JSON cannot distinguish them -- the
    catalog has to be asked directly. Getting it wrong is not cosmetic: it is the
    difference between "add an exception to validation-exceptions.yaml" and "an upstream
    file changed under us; roll CRD_CATALOG_REF back". 2026-10-08 was spent on that
    distinction, so it is made by measurement, not assumption.
    """
    if cache is not None and url in cache:
        return cache[url]
    try:
        status, body = fetch(url)
    except OSError as e:
        verdict = (SCHEMA_UNFETCHABLE, f"{type(e).__name__}: {str(e)[:100]}")
    else:
        if status == 404:
            verdict = (SCHEMA_MISSING, "")
        elif status != 200:
            verdict = (SCHEMA_UNFETCHABLE, f"HTTP {status} from {url}")
        else:
            try:
                json.loads(body)
                note = "parses as JSON"
            except ValueError:
                note = "does not even parse as JSON"
            verdict = (SCHEMA_UNUSABLE, f"{len(body):,} B at ref {CATALOG_REF[:12]}, {note}")
    if cache is not None:
        cache[url] = verdict
    return verdict


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


def report(label: str, data: dict, allowed_schemas: set, allowed_findings: list,
           fetch_cache: dict | None = None) -> int:
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
        # ⛔ Do NOT print one merged "no schema" list: it is wrong for the most expensive
        # case, where the schema EXISTS and upstream broke it. Ask the catalog and say
        # which of the three it is -- each has a different remedy, and only the first is
        # ours to fix in validation-exceptions.yaml.
        rc = 1
        buckets: dict[str, list] = collections.defaultdict(list)
        for (v, k), n in sorted(unexpected.items(), key=lambda x: -x[1]):
            verdict, detail = classify_schema(catalog_url(v, k), cache=fetch_cache)
            buckets[verdict].append((n, v, k, detail))

        for verdict, headline, remedy in (
            (SCHEMA_MISSING,
             "kinds with NO SCHEMA published for them (real coverage gap -- not a pass)",
             "add an entry to validation-exceptions.yaml with a reason, or find a schema"),
            (SCHEMA_UNUSABLE,
             "kinds whose schema EXISTS but kubeconform could not use it "
             "(an UPSTREAM catalog defect, NOT a coverage gap)",
             "roll CRD_CATALOG_REF back to a ref where it validates; do not except the "
             "kind, and do not 'fix' a manifest for this"),
            (SCHEMA_UNFETCHABLE,
             "kinds whose schema could not be FETCHED (availability, NOT a coverage gap)",
             "re-run the job; if it persists, check the catalog ref and outbound access"),
        ):
            rows = buckets.pop(verdict, [])
            if not rows:
                continue
            print(f"  {headline}:")
            for n, v, k, detail in rows:
                print(f"    {n:4d}  {v} {k}" + (f"  [{detail}]" if detail else ""))
            print(f"        -> {remedy}")

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
    fetch_cache: dict = {}   # one classification per kind, shared by both reports

    print(f"schema catalog: datree CRDs-catalog @ {CATALOG_REF}")

    manifests, skipped = tracked_manifests(root)
    print(f"tracked manifest files: {len(manifests)}")
    print(f"not validated (by design): {dict(skipped)}")
    rc |= report("tracked manifests", run_kubeconform(manifests, args.kube_version, args.kubeconform),
                 allowed_schemas, allowed_findings, fetch_cache)

    if args.rendered:
        rendered = Path(args.rendered)
        files = sorted(str(p) for p in rendered.glob("*.yaml"))
        if not files:
            print(f"\nno rendered manifests found in {rendered} -- did render-bundles.py run?")
            return 1
        print(f"\nrendered bundle files: {len(files)}")
        rc |= report("rendered bundles", run_kubeconform(files, args.kube_version, args.kubeconform),
                     allowed_schemas, allowed_findings, fetch_cache)

    print("\nValidation FAILED." if rc else "\nValidation passed.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
