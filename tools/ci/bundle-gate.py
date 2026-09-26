#!/usr/bin/env python3
"""Build every Fleet bundle offline and gate on buildability + size.

Two Fleet-specific failures that a schema check cannot see:

1. A chart ref that does not resolve. Fleet turns that into a Stalled GitRepo, and a
   Stall FREEZES bundling for the WHOLE repo until forceSyncGeneration -- so one bad
   version bump is a repo-wide outage, not a one-bundle outage.
2. A bundle too large for etcd ("etcdserver: request is too large"). The Bundle this
   produces is what goes to etcd, so its size is the real measurement.

`fleet apply -o -` needs no cluster and no kubeconfig.

⛔ The path MUST be relative to the repo root. Given an absolute path fleet exits 1 with
"no resource found at the following paths to deploy", which reads like a broken bundle
rather than a bad invocation.
"""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path

BUNDLE_MARKERS = ("fleet.yaml", "fleet.yml")
# etcd's default max request size is 1.5 MiB. Fleet compresses large bundles, but the
# margin is what matters: warn well before the wall.
WARN_BYTES = 800 * 1024
FAIL_BYTES = 1400 * 1024


def sh(a, cwd=None, env=None):
    return subprocess.run(a, cwd=cwd, env=env, text=True, capture_output=True, timeout=300)


def bundle_dirs(root: Path) -> list[str]:
    out = sh(["git", "ls-files", "-z"], cwd=root)
    found: dict[str, str] = {}
    for f in (x for x in out.stdout.split("\0") if x):
        base = os.path.basename(f)
        if base in BUNDLE_MARKERS:
            d = os.path.dirname(f)
            if d not in found or base == "fleet.yaml":
                found[d] = base
    return sorted(found)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", default=os.environ.get("FLEET", "fleet"))
    ap.add_argument("--warn-bytes", type=int, default=WARN_BYTES)
    ap.add_argument("--fail-bytes", type=int, default=FAIL_BYTES)
    ap.add_argument("--top", type=int, default=10, help="how many largest bundles to print")
    args = ap.parse_args()

    root = Path(sh(["git", "rev-parse", "--show-toplevel"]).stdout.strip())
    # fleet apply must never be able to reach a cluster from CI
    env = {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}

    failures: list[str] = []
    warnings: list[str] = []
    sizes: list[tuple[int, str]] = []

    dirs = bundle_dirs(root)
    for d in dirs:
        rel = "./" + d if d else "."
        res = sh([args.fleet, "apply", "-o", "-", "ci-" + (d.replace("/", "-") or "root"), rel],
                 cwd=root, env=env)
        if res.returncode != 0:
            last = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "no stderr"
            failures.append(f"{d}: fleet apply failed: {last}")
            continue
        n = len(res.stdout.encode())
        sizes.append((n, d))
        if n >= args.fail_bytes:
            failures.append(f"{d}: bundle is {n:,} bytes, over the {args.fail_bytes:,} limit (etcd request cap)")
        elif n >= args.warn_bytes:
            warnings.append(f"{d}: bundle is {n:,} bytes, past the {args.warn_bytes:,} warning mark")

    sizes.sort(reverse=True)
    print(f"built {len(sizes)}/{len(dirs)} bundle(s) offline")
    print(f"\nlargest {min(args.top, len(sizes))} bundles:")
    for n, d in sizes[:args.top]:
        print(f"  {n:>10,}  {d}")

    for w in warnings:
        print(f"\nWARNING: {w}")
    if failures:
        print("\nBundle gate failed:")
        for f in failures:
            print(f"- {f}")
        return 1
    print("\nBundle gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
