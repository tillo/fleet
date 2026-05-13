#!/usr/bin/env python3
"""
Lint: every VerticalPodAutoscaler in updateMode Auto/Initial that targets a
Deployment in this repo must have a matching `diff.comparePatches` entry in
its owning bundle's fleet.yaml — otherwise Fleet flags the bundle "Modified"
the moment VPA rewrites the pod's resources block.

Run from the repo root:
    python3 scripts/check-vpa-coverage.py

Exits non-zero with a punch-list when coverage is missing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
VPA_DIR = REPO / "vpa-objects"

# VPAs we know don't need patching: target is a StatefulSet (different patch
# path) or a Deployment that fleet doesn't own (e.g. operator-managed CR).
# Add explicit skips as we hit them.
SKIP_TARGETS: set[tuple[str, str, str]] = set()  # (kind, ns, name)


def load_vpas() -> list[dict]:
    out = []
    for f in sorted(VPA_DIR.glob("*.yaml")):
        with f.open() as fh:
            for doc in yaml.safe_load_all(fh):
                if not doc:
                    continue
                if doc.get("kind") != "VerticalPodAutoscaler":
                    continue
                out.append(doc)
    return out


def find_fleet_yamls() -> list[Path]:
    out = []
    for p in REPO.rglob("fleet.y*ml"):
        # Skip the vpa-objects bundle itself.
        if "vpa-objects" in p.parts:
            continue
        out.append(p)
    return out


def fleet_has_compare_patch(fleet_yaml: Path, kind: str, ns: str, name: str) -> bool:
    """Return True if this fleet.yaml has a diff.comparePatches entry that
    strips the resources block on the named target."""
    try:
        with fleet_yaml.open() as fh:
            bundle = yaml.safe_load(fh) or {}
    except Exception:
        return False
    patches = (bundle.get("diff") or {}).get("comparePatches") or []
    for p in patches:
        if p.get("kind") != kind:
            continue
        if p.get("name") != name:
            continue
        if p.get("namespace") != ns:
            continue
        for op in p.get("operations", []) or []:
            if (op.get("op") == "remove"
                    and "/resources" in str(op.get("path", ""))):
                return True
    return False


def find_owning_fleet_yaml(ns: str, name: str, fleets: list[Path]) -> Path | None:
    """Best-effort: a fleet.yaml owns the target if its defaultNamespace
    matches and one of its sibling files defines the resource."""
    for f in fleets:
        try:
            with f.open() as fh:
                bundle = yaml.safe_load(fh) or {}
        except Exception:
            continue
        if bundle.get("defaultNamespace") != ns and bundle.get("namespace") != ns:
            continue
        # Check siblings for a manifest defining this resource.
        for s in f.parent.rglob("*.y*ml"):
            if s == f:
                continue
            try:
                with s.open() as fh:
                    for doc in yaml.safe_load_all(fh):
                        if not doc:
                            continue
                        if doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet"):
                            continue
                        if (doc.get("metadata", {}) or {}).get("name") == name:
                            return f
            except Exception:
                continue
    return None


def main() -> int:
    vpas = load_vpas()
    fleets = find_fleet_yamls()

    missing: list[tuple[str, str, str, str, Path | None]] = []
    skipped: list[tuple[str, str, str, str]] = []

    for vpa in vpas:
        meta = vpa.get("metadata", {}) or {}
        spec = vpa.get("spec", {}) or {}
        target = spec.get("targetRef", {}) or {}
        update = (spec.get("updatePolicy", {}) or {}).get("updateMode", "Auto")

        kind = target.get("kind", "")
        ns = target.get("namespace") or meta.get("namespace") or ""
        name = target.get("name", "")

        if update not in ("Auto", "Initial"):
            continue  # Off / Recreate-only — no in-place mutation; no drift
        if (kind, ns, name) in SKIP_TARGETS:
            skipped.append((kind, ns, name, "in SKIP_TARGETS"))
            continue
        if kind != "Deployment":
            # comparePatches paths for StatefulSet differ. For now, alert
            # the operator; manual review.
            skipped.append((kind, ns, name, f"non-Deployment target ({kind}) — review manually"))
            continue

        owner = find_owning_fleet_yaml(ns, name, fleets)
        if owner is None:
            skipped.append((kind, ns, name, "no fleet.yaml found that owns this target"))
            continue
        if not fleet_has_compare_patch(owner, kind, ns, name):
            missing.append((kind, ns, name, str(owner.relative_to(REPO)), update))

    if skipped:
        print("Skipped (review manually):", file=sys.stderr)
        for kind, ns, name, why in skipped:
            print(f"  {kind} {ns}/{name}: {why}", file=sys.stderr)
        print(file=sys.stderr)

    if not missing:
        print(f"OK — {len(vpas)} VPAs checked, all Auto/Initial Deployment targets have coverage.")
        return 0

    print(f"FAIL — {len(missing)} VPA(s) missing diff.comparePatches:")
    for kind, ns, name, owner, update in missing:
        print(f"  {kind} {ns}/{name} (VPA updateMode={update}) — add to {owner}")
        print(f"      diff:")
        print(f"        comparePatches:")
        print(f"        - apiVersion: apps/v1")
        print(f"          kind: {kind}")
        print(f"          name: {name}")
        print(f"          namespace: {ns}")
        print(f"          operations:")
        print(f"          - {{\"op\": \"remove\", \"path\": \"/spec/template/spec/containers/0/resources\"}}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
