#!/usr/bin/env python3
"""Check every PrometheusRule expression with PROMETHEUS' own parser.

The estate EVALUATES rules with VictoriaMetrics (vmalert), whose MetricsQL is a
superset of PromQL. So `(a - b) offset 24h` returns a vector from vmsingle and
passes `vmalert-tool` — the two checks this repo had — and is still invalid
PromQL. What rejects it is the prometheus-operator admission webhook, i.e. the
cluster, after the merge.

Measured 2026-11-08 (fleet !2069): `app-batch-jobs` — 211 alerts including
`BatchJobStale` and `PushgatewayGroupStale` — was DELETED from the cluster.
Fleet's Helm release rolled back after the webhook refused the file on every
retry, and the recreate failed the same way, so nothing paged about a dead
batch job for ~20 minutes. Every gate in this pipeline was green:
`rule-tests.py` runs vmalert-tool (MetricsQL), `validate-manifests.py` runs
kubeconform (schema only, expressions are opaque strings).

promtool is the missing parser. It is offline, needs no cluster credentials, and
reads exactly the shape a PrometheusRule carries: `spec.groups`.

usage: PROMTOOL=.bin/promtool python3 tools/ci/promql-rules.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).absolute().parent.parent.parent
RULES = ROOT / "monitoring-rules"


def tracked_rule_files() -> list[Path]:
    """Every TRACKED yml under monitoring-rules — the same set CI sees."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "monitoring-rules/*.yml", "monitoring-rules/*.yaml"],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return sorted(ROOT / p for p in out.split("\0") if p)


def rule_documents(path: Path) -> list[dict]:
    """The PrometheusRule documents in a file, as {groups: [...]} rule files."""
    docs = [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    return [{"groups": d["spec"]["groups"]} for d in docs if d.get("kind") == "PrometheusRule"]


def main() -> int:
    tool = os.environ.get("PROMTOOL", "promtool")
    files = tracked_rule_files()
    if not files:
        print("no monitoring-rules/*.yml found", file=sys.stderr)
        return 1
    checked = failed = 0
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        for path in files:
            for i, doc in enumerate(rule_documents(path)):
                checked += 1
                dest = scratch / f"{path.stem}-{i}.yml"
                dest.write_text(yaml.safe_dump(doc, sort_keys=False))
                r = subprocess.run([tool, "check", "rules", str(dest)],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    continue
                failed += 1
                print(f"FAILED {path.relative_to(ROOT)}", file=sys.stderr)
                print((r.stdout + r.stderr).strip(), file=sys.stderr)
    print(f"{checked} PrometheusRule document(s) checked with promtool, {failed} invalid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
