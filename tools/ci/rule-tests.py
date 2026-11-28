#!/usr/bin/env python3
"""Run every alert-rule unit test in tests/rules/*.test.yml with vmalert-tool.

A test names the monitoring-rules PrometheusRule files it exercises in `rule_files`,
exactly as they sit in this repo. vmalert-tool reads plain vmalert rule files, so each
PrometheusRule's `spec.groups` is extracted to a scratch copy and the test is pointed at
it; the rule under test is therefore byte-for-byte the expression the cluster loads.

⚠️ vmalert-tool's clock starts at 2000-01-01T00:00:00Z (time() = 946684800 + eval_time):
an input series that carries a TIMESTAMP as its value (kube_pod_created, *_last_run…)
must be written on that origin.

usage: VMALERT_TOOL=.bin/vmalert-tool [RULE_TEST_JOBS=n] python3 tools/ci/rule-tests.py
(files run concurrently, os.cpu_count() at a time by default)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).absolute().parent.parent.parent


def vmalert_rule_file(doc: dict) -> dict:
    if doc.get("kind") != "PrometheusRule":
        raise ValueError(f"not a PrometheusRule: kind={doc.get('kind')!r}")
    return {"groups": doc["spec"]["groups"]}


def prometheus_rule(text: str) -> dict:
    """The one PrometheusRule in a rule file; other documents (a ServiceMonitor) are skipped."""
    rules = [d for d in yaml.safe_load_all(text) if d and d.get("kind") == "PrometheusRule"]
    if len(rules) != 1:
        raise ValueError(f"expected exactly one PrometheusRule, found {len(rules)}")
    return rules[0]


def prepare(test: dict, root: Path, out: Path) -> dict:
    """Return a copy of `test` whose rule_files point at extracted vmalert rule files in `out`."""
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i, rel in enumerate(test.get("rule_files") or []):
        doc = prometheus_rule((root / rel).read_text())
        dest = out / f"{i}-{Path(rel).name}"
        dest.write_text(yaml.safe_dump(vmalert_rule_file(doc)))
        files.append(str(dest))
    return {**test, "rule_files": files}


def run_one(test: Path, root: Path, tool: str, scratch: Path) -> tuple[str, int, str]:
    """Run one test file; return (name, exit code, its log block)."""
    work = scratch / test.stem
    prepared = prepare(yaml.safe_load(test.read_text()), root, work)
    runnable = work / test.name
    runnable.write_text(yaml.safe_dump(prepared))
    proc = subprocess.run([tool, "unittest", "--files", str(runnable)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return test.name, proc.returncode, f"== {test.relative_to(root)}\n{proc.stdout}"


def run_all(root: Path, tool: str, jobs: int) -> tuple[list[str], str, int]:
    """Run every tests/rules/*.test.yml concurrently; return (failed, log in file order, total).

    The files are independent: vmalert-tool binds a random free port and keeps its storage
    in memory. Run one after another they cost ~14 s each, 819 s of fleet-validate in all."""
    tests = sorted((root / "tests" / "rules").glob("*.test.yml"))
    if not tests:
        raise FileNotFoundError("no tests/rules/*.test.yml")
    with tempfile.TemporaryDirectory() as d, ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(lambda t: run_one(t, root, tool, Path(d)), tests))
    failed = [name for name, rc, _ in results if rc != 0]
    return failed, "".join(log for _, _, log in results), len(results)


def summary(failed: list[str], total: int) -> str:
    line = f"{total - len(failed)}/{total} rule test files passed"
    return line + (f"; FAILED: {', '.join(failed)}" if failed else "")


def worker_count(setting: str | None, cpus: int | None) -> int:
    """RULE_TEST_JOBS when set, else the visible CPUs, else one. A setting of 0 is refused
    by the thread pool rather than silently rounded up."""
    return int(setting or cpus or 1)


def main() -> int:
    tool = os.environ.get("VMALERT_TOOL", "vmalert-tool")
    jobs = worker_count(os.environ.get("RULE_TEST_JOBS"), os.cpu_count())
    try:
        failed, log, total = run_all(ROOT, tool, jobs)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    print(log + summary(failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
