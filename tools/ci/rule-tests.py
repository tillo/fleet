#!/usr/bin/env python3
"""Run every alert-rule unit test in tests/rules/*.test.yml with vmalert-tool.

A test names the monitoring-rules PrometheusRule files it exercises in `rule_files`,
exactly as they sit in this repo. vmalert-tool reads plain vmalert rule files, so each
PrometheusRule's `spec.groups` is extracted to a scratch copy and the test is pointed at
it; the rule under test is therefore byte-for-byte the expression the cluster loads.

⚠️ vmalert-tool's clock starts at 2000-01-01T00:00:00Z (time() = 946684800 + eval_time):
an input series that carries a TIMESTAMP as its value (kube_pod_created, *_last_run…)
must be written on that origin.

usage: VMALERT_TOOL=.bin/vmalert-tool python3 tools/ci/rule-tests.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).absolute().parent.parent.parent


def vmalert_rule_file(doc: dict) -> dict:
    if doc.get("kind") != "PrometheusRule":
        raise ValueError(f"not a PrometheusRule: kind={doc.get('kind')!r}")
    return {"groups": doc["spec"]["groups"]}


def prepare(test: dict, root: Path, out: Path) -> dict:
    """Return a copy of `test` whose rule_files point at extracted vmalert rule files in `out`."""
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i, rel in enumerate(test.get("rule_files") or []):
        doc = yaml.safe_load((root / rel).read_text())
        dest = out / f"{i}-{Path(rel).name}"
        dest.write_text(yaml.safe_dump(vmalert_rule_file(doc), sort_keys=False))
        files.append(str(dest))
    return {**test, "rule_files": files}


def main() -> int:
    tool = os.environ.get("VMALERT_TOOL", "vmalert-tool")
    tests = sorted((ROOT / "tests" / "rules").glob("*.test.yml"))
    if not tests:
        print("no tests/rules/*.test.yml", file=sys.stderr)
        return 1
    failed = []
    with tempfile.TemporaryDirectory() as d:
        for t in tests:
            work = Path(d) / t.stem
            prepared = prepare(yaml.safe_load(t.read_text()), ROOT, work)
            runnable = work / t.name
            runnable.write_text(yaml.safe_dump(prepared, sort_keys=False))
            print(f"== {t.relative_to(ROOT)}", flush=True)
            if subprocess.run([tool, "unittest", "--files", str(runnable)]).returncode != 0:
                failed.append(t.name)
    print(f"{len(tests) - len(failed)}/{len(tests)} rule test files passed"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
