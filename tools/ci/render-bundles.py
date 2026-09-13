#!/usr/bin/env python3
"""Render every Fleet bundle in this repo to plain Kubernetes manifests.

Fleet templates Helm charts agent-side at deploy time, so nothing in git or in the
Bundle object is a rendered manifest. This reproduces that render in CI so the output
can be schema-checked and linted before a merge.

Bundle discovery is by `git ls-files` and accepts BOTH `fleet.yaml` and `fleet.yml`
(Fleet reads either; this repo uses .yml for 32 of 153 bundles).

Unsupported bundle options FAIL LOUDLY rather than render something that is not what
Fleet would deploy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

BUNDLE_MARKERS = ("fleet.yaml", "fleet.yml")

# Bundle-level keys this renderer understands. Anything else in a `helm:` block that
# changes the rendered output must be handled here, not ignored -- see UNSUPPORTED.
HELM_KNOWN = {
    "chart", "repo", "version", "releaseName", "values", "valuesFiles",
    "maxHistory", "timeoutSeconds", "force", "takeOwnership", "atomic",
    "disablePreProcess", "waitForJobs", "skipSchemaValidation", "disableDNS",
}
# Keys that would make a plain `helm template` diverge from what Fleet deploys.
HELM_UNSUPPORTED = {"valuesFrom"}


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=300)


def repo_root() -> Path:
    out = sh(["git", "rev-parse", "--show-toplevel"])
    if out.returncode != 0:
        sys.exit("not a git repository")
    return Path(out.stdout.strip())


def bundle_dirs(root: Path) -> list[tuple[Path, Path]]:
    """Return (bundle_dir, marker_file) for every Fleet bundle, recursively."""
    out = sh(["git", "ls-files", "-z"], cwd=root)
    files = [f for f in out.stdout.split("\0") if f]
    found: dict[str, str] = {}
    for f in files:
        base = os.path.basename(f)
        if base in BUNDLE_MARKERS:
            d = os.path.dirname(f)
            # fleet.yaml wins if a directory somehow has both
            if d not in found or base == "fleet.yaml":
                found[d] = f
    return [(root / d, root / m) for d, m in sorted(found.items())]


def slug(root: Path, d: Path) -> str:
    rel = d.relative_to(root).as_posix()
    return rel.replace("/", "__") or "_root"


def helm_env(scratch: Path) -> dict:
    """Hermetic Helm: never read the caller's repo list or cache.

    Without this, `helm template --repo ...` still consults the local repository
    config and dies on an unrelated stale entry ("no cached repo found ...
    rancher-charts-index.yaml"), which looks like a broken bundle.
    """
    hc = scratch / "helm-config"
    hcache = scratch / "helm-cache"
    hdata = scratch / "helm-data"
    for p in (hc, hcache, hdata):
        p.mkdir(parents=True, exist_ok=True)
    (hc / "repositories.yaml").write_text("")
    env = dict(os.environ)
    env.update({
        "HELM_REPOSITORY_CONFIG": str(hc / "repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(hcache),
        "HELM_DATA_HOME": str(hdata),
        "HELM_CACHE_HOME": str(hcache),
    })
    return env


def render_helm(root: Path, d: Path, spec: dict, scratch: Path, kube_version: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    helm = spec.get("helm") or {}
    chart = helm["chart"]

    unsupported = HELM_UNSUPPORTED & set(helm)
    if unsupported:
        errors.append(f"{d.name}: helm.{sorted(unsupported)} is not reproducible by `helm template`")
        return "", errors
    unknown = set(helm) - HELM_KNOWN
    if unknown:
        errors.append(f"{d.name}: unrecognised helm keys {sorted(unknown)} -- teach the renderer or add to HELM_KNOWN")
        return "", errors

    release = helm.get("releaseName") or d.name
    namespace = spec.get("namespace") or spec.get("defaultNamespace") or "default"

    args = [os.environ.get("HELM", "helm"), "template", release]
    # A chart that resolves to a path inside the bundle is a local chart.
    local = (d / str(chart)).resolve()
    if local.exists():
        args.append(str(local))
    else:
        args.append(str(chart))
        if helm.get("repo"):
            args += ["--repo", str(helm["repo"])]
    if helm.get("version"):
        args += ["--version", str(helm["version"])]
    args += ["--namespace", namespace, "--include-crds", "--kube-version", kube_version]

    for vf in helm.get("valuesFiles") or []:
        p = d / vf
        if not p.exists():
            errors.append(f"{d.name}: valuesFiles entry {vf!r} does not exist")
            return "", errors
        args += ["-f", str(p)]

    if helm.get("values") is not None:
        inline = scratch / f"{slug(root, d)}-inline-values.yaml"
        inline.write_text(yaml.safe_dump(helm["values"]))
        args += ["-f", str(inline)]

    res = sh(args, cwd=d, env=helm_env(scratch))
    if res.returncode != 0:
        errors.append(f"{d.name}: helm template failed: {res.stderr.strip().splitlines()[-1] if res.stderr.strip() else 'no stderr'}")
        return "", errors
    return res.stdout, errors


def apply_kustomize(root: Path, d: Path, spec: dict, rendered: str, scratch: Path) -> tuple[str, list[str]]:
    """Reproduce Fleet's helm->kustomize post-render.

    Fleet feeds the helm-rendered manifests in as the kustomize base, then applies the
    bundle's kustomization on top. Verified against metallb 2026-09-12: the frr
    container's livenessProbe.timeoutSeconds is absent from the chart output and
    becomes 5 after the post-render, which is exactly what the patch claims to do.
    """
    errors: list[str] = []
    kdir_name = (spec.get("kustomize") or {}).get("dir")
    src = d / kdir_name if kdir_name else d
    if not (src / "kustomization.yaml").exists() and not (src / "kustomization.yml").exists():
        errors.append(f"{d.name}: kustomize dir {src} has no kustomization.yaml")
        return "", errors

    work = scratch / f"kustomize-{slug(root, d)}"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work)
    (work / "__helm-rendered.yaml").write_text(rendered)

    kfile = work / "kustomization.yaml"
    if not kfile.exists():
        kfile = work / "kustomization.yml"
    kdoc = yaml.safe_load(kfile.read_text()) or {}
    kdoc.setdefault("resources", [])
    kdoc["resources"].insert(0, "__helm-rendered.yaml")
    kfile.write_text(yaml.safe_dump(kdoc, sort_keys=False))

    res = sh([os.environ.get("KUBECTL", "kubectl"), "kustomize", str(work)])
    if res.returncode != 0:
        errors.append(f"{d.name}: kubectl kustomize failed: {res.stderr.strip().splitlines()[-1] if res.stderr.strip() else 'no stderr'}")
        return "", errors
    return res.stdout, errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, help="empty directory to write rendered manifests into")
    ap.add_argument("--kube-version", default="1.35.2", help="cluster version for chart .Capabilities")
    ap.add_argument("--bundle", action="append", help="render only this bundle dir (repeatable)")
    args = ap.parse_args()

    root = repo_root()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        sys.exit(f"{out} is not empty; rendered output may contain chart-generated Secrets")

    scratch = Path(tempfile.mkdtemp(prefix="fleet-render-"))
    errors: list[str] = []
    n_helm = n_kust = n_raw = 0

    try:
        for d, marker in bundle_dirs(root):
            rel = d.relative_to(root).as_posix()
            if args.bundle and rel not in args.bundle:
                continue
            try:
                spec = yaml.safe_load(marker.read_text()) or {}
            except yaml.YAMLError as exc:
                errors.append(f"{rel}: {marker.name} does not parse: {exc}")
                continue
            if not isinstance(spec, dict):
                errors.append(f"{rel}: {marker.name} is not a mapping")
                continue

            has_chart = bool((spec.get("helm") or {}).get("chart"))
            if not has_chart:
                n_raw += 1
                continue

            rendered, errs = render_helm(root, d, spec, scratch, args.kube_version)
            errors += errs
            if errs:
                continue
            n_helm += 1

            if spec.get("kustomize"):
                rendered, errs = apply_kustomize(root, d, spec, rendered, scratch)
                errors += errs
                if errs:
                    continue
                n_kust += 1

            (out / f"{slug(root, d)}.yaml").write_text(rendered)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"rendered {n_helm} chart bundle(s) ({n_kust} with kustomize post-render); "
          f"{n_raw} raw-manifest bundle(s) need no render")
    if errors:
        print("\nRender failures:")
        for e in errors:
            print(f"- {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
