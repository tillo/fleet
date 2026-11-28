"""Estimate the size of the Helm release Secret Fleet will store for a bundle.

Fleet deploys each bundle as a Helm release and Helm keeps each revision in ONE
Secret, which the API server refuses above 1,048,576 bytes of data. On 2026-11-19
mdapi-prod-monitoring-rules crossed it (infra-3rm.33): the bundle went ErrApplied and
every later merge to it deployed nothing.

What Helm stores (decoded from live releases): base64(gzip(JSON)) whose JSON holds
the TOP chart -- templates base64'd, its default values (232 KB of JSON for gitlab),
Chart.yaml, README/LICENSE/crds files -- the supplied values ("config"), and the
rendered manifest. Subcharts are NOT stored; they appear only in the manifest.
For a raw-manifest bundle Fleet builds the chart from the bundle's own files, so
every byte is stored about twice.

Calibrated 2026-11-20 against live releases: mdapi-prod-monitoring-rules within 1%
(raw bundle); kagent-crds (remote OCI chart, two CRD subcharts) and gitlab (vendored
local chart with kustomize post-render) -- figures in tests/test_release_size.py.
gzip level 9 lands closest to Helm's output.

Used by tests/test_every_bundle_release_fits_the_secret_limit.py (raw bundles, from
the repo) and tools/ci/render-bundles.py (chart bundles, from the pulled chart and
its render).
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from pathlib import Path

import yaml

SECRET_DATA_LIMIT = 1048576

## Fail at 90%: monitoring-rules grew ~6 KB per alert group; 90% leaves ~100 KB,
## so a split is forced while there is still room to land it in one MR.
FAIL_RATIO = 0.90

MARKERS = ("fleet.yaml", "fleet.yml")


def _derived_manifest(files: dict[str, bytes]) -> str:
    docs = []
    for name, content in files.items():
        if name.endswith((".yaml", ".yml")):
            try:
                docs.extend(d for d in yaml.safe_load_all(content) if d)
            except yaml.YAMLError:
                pass
    return "---\n".join(yaml.safe_dump(d) for d in docs)


def estimate(files: dict[str, bytes], manifest: str | None = None) -> int:
    """Bytes of the release Secret's data.

    files: chart-relative path -> content (a raw bundle's files, or a chart's
    templates). manifest: the rendered output; derived from the YAML files when
    omitted, which is what Fleet does for a raw-manifest bundle. Bundle markers are
    not chart content.
    """
    kept = {n: c for n, c in sorted(files.items()) if os.path.basename(n) not in MARKERS}
    if manifest is None:
        manifest = _derived_manifest(kept)
    chart = [{"name": n, "data": base64.b64encode(c).decode()} for n, c in kept.items()]
    blob = json.dumps({"chart": {"templates": chart}, "manifest": manifest}).encode()
    return 4 * -(-len(gzip.compress(blob, 9)) // 3)  # helm base64-encodes the gzip


def chart_files(chart_dir: Path) -> dict[str, bytes]:
    """The files Helm stores for a chart: all of the top chart, never charts/ (subcharts)."""
    return {p.relative_to(chart_dir).as_posix(): p.read_bytes()
            for p in sorted(chart_dir.rglob("*"))
            if p.is_file() and p.relative_to(chart_dir).parts[0] != "charts"}


def oversized(estimates: dict[str, int]) -> list[str]:
    """One message per bundle at or above FAIL_RATIO of the limit."""
    limit = int(SECRET_DATA_LIMIT * FAIL_RATIO)
    return [f"{d}/: estimated release Secret {n:,} B = {100 * n / SECRET_DATA_LIMIT:.1f}% "
            f"of the 1 MiB limit (fails at {100 * FAIL_RATIO:.0f}%). Split the bundle "
            f"(see monitoring-rules/batch-jobs/fleet.yaml), or for an upstream chart "
            f"drop what it does not need to ship."
            for d, n in sorted(estimates.items()) if n >= limit]
