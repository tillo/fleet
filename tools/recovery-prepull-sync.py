#!/usr/bin/env python3
"""Rewrite the LIVE-DERIVED pre-puller files in jump/seat-prepull/ from what the cluster RUNS.

Two sets, each one DaemonSet file whose marked block this tool owns:
  recovery           recovery-image-prepull.yml -- the internet-sourced recovery images:
                     cert-manager, MetalLB, external-secrets, the CNPG operator + barman
                     plugin, and the seat's web door (guacamole, guacd, guac-pg).
  zot-critical-live  zot-critical-live-prepull.yml -- the critical zot-served images with
                     no single git line to mirror: oauth2-proxy + its redis session
                     stores, and vmsingle/vmagent/vmalert/vmalertmanager.
Derived from the LIVE pods rather than from git or the charts: these workloads pull by
TAG (or take it from an operator default), so the digest a node runs is whatever it
pulled, and CNPG picks its bootstrap and sidecar images at runtime, which no chart render
shows. Alerts RecoveryImageNotPrepulled / ZotCriticalImageNotPrepulled
(monitoring-rules/88-seat-image-prepull.yml) fire when a set and the cluster disagree;
this is the fix.

usage:
  python3 tools/recovery-prepull-sync.py                 # rewrite every set from mdapi-prod-direct
  python3 tools/recovery-prepull-sync.py --set recovery  # one set (repeatable)
  python3 tools/recovery-prepull-sync.py --check         # exit 1 if any set differs from live
  python3 tools/recovery-prepull-sync.py --pods pods.json   # derive from a saved `kubectl get pods -A -o json`
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent.parent / "jump" / "seat-prepull"
BEGIN, END = "# BEGIN derived", "# END derived"
HOLD_BIN = "/hold/busybox"
INDENT = "      "
DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")
ZOT = "zot.mdapi.ch/"


class Scope(NamedTuple):
    """Pods of one namespace whose name matches `pod`; only images starting with `prefix`."""
    namespace: str
    pod: str = ""
    prefix: str = ""

    def admits(self, meta: dict) -> bool:
        return meta.get("namespace") == self.namespace and bool(re.match(self.pod, meta.get("name", "")))


class PrepullSet(NamedTuple):
    path: Path
    scopes: tuple[Scope, ...]


RECOVERY = tuple(Scope(ns) for ns in ("cert-manager", "metallb-system", "external-secrets", "cnpg-system")) + (
    Scope("jump", r"(guacamole|guacd|guac-pg)-"),)
ZOT_CRITICAL_LIVE = (
    Scope("oauth2-proxy", r"oauth2-proxy-", ZOT),
    Scope("monitoring", r"(vmsingle-mdapi|vmagent-mdapi|vmalert-mdapi|vmalertmanager-mdapi)-", ZOT),
)
SETS = {
    "recovery": PrepullSet(HERE / "recovery-image-prepull.yml", RECOVERY),
    "zot-critical-live": PrepullSet(HERE / "zot-critical-live-prepull.yml", ZOT_CRITICAL_LIVE),
}


def pinned(spec_image: str, image_id: str) -> str | None:
    """repo:tag@digest of what this container runs; None until the image is pulled."""
    if "@sha256:" in spec_image:
        return spec_image
    found = DIGEST.search(image_id or "")
    if not found:
        return None
    if ":" not in spec_image.rsplit("/", 1)[-1]:
        raise ValueError(f"untagged image {spec_image!r}: a pin needs a tag for humans")
    return f"{spec_image}@{found.group(1)}"


def pod_refs(pod: dict, prefix: str = "") -> set[str]:
    if pod.get("status", {}).get("phase") != "Running":
        return set()
    spec = pod.get("spec", {})
    images = {c["name"]: c["image"] for c in spec.get("containers", []) + spec.get("initContainers", [])}
    statuses = pod["status"].get("containerStatuses", []) + pod["status"].get("initContainerStatuses", [])
    refs = {pinned(images.get(s["name"], s.get("image", "")), s.get("imageID", "")) for s in statuses}
    return {r for r in refs if r and r.startswith(prefix)}


def derive(pods: list[dict], scopes: tuple[Scope, ...] = RECOVERY) -> list[str]:
    """Every pinned reference the in-scope pods run, sorted, once each."""
    refs: set[str] = set()
    for p in pods:
        meta = p.get("metadata", {})
        for scope in scopes:
            if scope.admits(meta):
                refs |= pod_refs(p, scope.prefix)
    return sorted(refs)


def container_names(refs: list[str]) -> list[str]:
    """One DNS-label container name per reference, unique, readable (the repo basename)."""
    names: list[str] = []
    for ref in refs:
        base = re.sub(r"[^a-z0-9]", "-", ref.split("@")[0].rsplit(":", 1)[0].rsplit("/", 1)[-1].lower())
        base = base[:56].strip("-")
        name, n = base, 2
        while name in names:
            name, n = f"{base}-{n}", n + 1
        names.append(name)
    return names


# Every holder is identical but for name and image: it only sleeps, from the staged
# static busybox, read-only, no privileges, a few MiB.
HOLDER = [
    f'  command: ["{HOLD_BIN}", "sleep", "2147483647"]',
    "  volumeMounts: [{ name: hold, mountPath: /hold, readOnly: true }]",
    '  securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } }',
    "  resources: { requests: { cpu: 1m, memory: 2Mi }, limits: { memory: 16Mi } }",
]


def block(refs: list[str]) -> str:
    out = []
    for name, ref in zip(container_names(refs), refs):
        out += [f"- name: {name}", f"  image: {ref}", "  imagePullPolicy: IfNotPresent", *HOLDER]
    return "".join(f"{INDENT}{line}\n" for line in out)


def rewrite(text: str, refs: list[str]) -> str:
    """Replace the lines between the BEGIN/END markers; everything else is kept byte for byte."""
    lines = text.splitlines(keepends=True)
    begin = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    end = [i for i, line in enumerate(lines) if line.strip() == END]
    if not (len(begin) == len(end) == 1 and begin[0] < end[0]):
        raise ValueError(f"need exactly one {BEGIN!r} line followed by one {END!r} line")
    return "".join(lines[:begin[0] + 1]) + block(refs) + "".join(lines[end[0]:])


def live_pods() -> list[dict]:
    out = subprocess.run(["kubectl", "--context", "mdapi-prod-direct", "get", "pods", "-A", "-o", "json"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["items"]


def sync_one(name: str, pods: list[dict], check: bool) -> int:
    target = SETS[name]
    refs = derive(pods, target.scopes)
    if not refs:
        print(f"{name}: no pods found -- refusing to write an empty pre-puller", file=sys.stderr)
        return 2
    text = target.path.read_text(encoding="utf-8")
    new = rewrite(text, refs)
    if check:
        print(f"{name}: " + ("in sync" if new == text else "DRIFT: run tools/recovery-prepull-sync.py"))
        return 0 if new == text else 1
    target.path.write_text(new, encoding="utf-8")
    print(f"{name}: {len(refs)} images written to {target.path.name}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if a set differs from live")
    ap.add_argument("--set", action="append", choices=sorted(SETS), help="only this set (repeatable)")
    ap.add_argument("--pods", help="read pods from this JSON file instead of the cluster")
    args = ap.parse_args(argv)
    pods = json.loads(Path(args.pods).read_text())["items"] if args.pods else live_pods()
    return max(sync_one(name, pods, args.check) for name in args.set or sorted(SETS))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
