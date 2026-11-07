#!/usr/bin/env python3
"""Every image digest a commit range ADDS must resolve to a linux/amd64 image (infra-r9mzd.23).

Incident: fleet 80788d98 automerged library/caddy:2.11.8-alpine@sha256:eb37f586..., which
Docker Hub serves as an OCI index with zero manifests; k8s-mcp had 0 ready replicas for
3.5 h. Nothing asked the registry. This step does, for each `image: <ref>@sha256:<hex>`
line added since the base, and fails unless the digest is (or indexes) linux/amd64.

  python3 tools/ci/image-platforms.py [BASE]   # default: the MR diff base, else HEAD~1

The in-house registries (registry.mdapi.ch, zot.mdapi.ch) need credentials and only serve
images our own amd64 CI built, so they are reported as skipped. A public registry that
cannot be reached is a FAILURE: an unverified digest is exactly what this gate refuses.
Helm values that split repository/tag/digest are not seen (only `image:` lines).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PLATFORM = ("linux", "amd64")
IN_HOUSE = ("registry.mdapi.ch", "zot.mdapi.ch")
DOCKER_HUB = "registry-1.docker.io"
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
ACCEPT = ", ".join(INDEX_TYPES + ("application/vnd.oci.image.manifest.v1+json",
                                  "application/vnd.docker.distribution.manifest.v2+json"))

ADDED_IMAGE = re.compile(
    r"""^\+(?!\+\+)\s*(?:-\s*)?image:\s*["']?(?P<ref>[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64})""")


def added_refs(diff: str) -> list[str]:
    """Image refs with a digest on lines the diff ADDS, sorted and unique."""
    return sorted({m["ref"] for m in map(ADDED_IMAGE.match, diff.splitlines()) if m})


def split_ref(ref: str) -> tuple[str, str, str]:
    """(registry host, repository, 'sha256:...'), Docker Hub short names expanded."""
    name, _, digest = ref.partition("@")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, repo = first, rest
    else:
        host, repo = DOCKER_HUB, name
    if host == "docker.io":
        host = DOCKER_HUB
    repo = repo.split(":", 1)[0]
    if host == DOCKER_HUB and "/" not in repo:
        repo = f"library/{repo}"
    return host, repo, digest


def _platforms(manifest: dict) -> list[str]:
    return [f"{p.get('os')}/{p.get('architecture')}"
            for p in (m.get("platform") or {} for m in manifest.get("manifests", []))]


def verdict(manifest: dict, config: dict | None) -> str | None:
    """None when the digest provides linux/amd64, else why not."""
    want = "/".join(PLATFORM)
    if manifest.get("mediaType") in INDEX_TYPES or "manifests" in manifest:
        found = _platforms(manifest)
        return None if want in found else f"no {want} in its index (has: {', '.join(found) or 'nothing'})"
    got = f"{(config or {}).get('os')}/{(config or {}).get('architecture')}"
    return None if got == want else f"single-platform image is {got}, not {want}"


def parse_bearer(header: str) -> tuple[str, dict] | None:
    """(realm, params) of a `WWW-Authenticate: Bearer ...` challenge, else None."""
    if not header.startswith("Bearer "):
        return None
    params = dict(re.findall(r'(\w+)="([^"]*)"', header))
    realm = params.pop("realm", None)
    return (realm, params) if realm else None


class Result:
    """What check() found. (No dataclass: tests load this file by path, outside sys.modules.)"""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []
        self.skipped: list[str] = []


def check(refs, fetch_manifest, fetch_config) -> Result:
    """Judge each ref; the two fetchers are the only I/O."""
    out = Result()
    for ref in refs:
        host, repo, digest = split_ref(ref)
        if host in IN_HOUSE:
            out.skipped.append(f"{ref}: in-house registry, built by our amd64 CI")
            continue
        try:
            manifest = fetch_manifest(host, repo, digest)
            config = None
            if "manifests" not in manifest:   # a single manifest: its config says the platform
                config = fetch_config(host, repo, manifest["config"]["digest"])
            why = verdict(manifest, config)
        except (OSError, ValueError, KeyError) as e:
            why = f"could not be verified: {e}"
        if why:
            out.failures.append(f"{ref}: {why}")
        else:
            out.passed += 1
    return out


# --- registry I/O (the boundary) ---------------------------------------------------------

def _get(url: str, accept: str, token: str | None = None):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "fleet-ci-image-platforms"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=30)


def _token(challenge: str) -> str:
    parsed = parse_bearer(challenge)
    if not parsed:
        raise ValueError(f"registry wants non-bearer auth: {challenge[:80]!r}")
    realm, params = parsed
    with urllib.request.urlopen(f"{realm}?{urllib.parse.urlencode(params)}", timeout=30) as r:
        body = json.load(r)
    return body.get("token") or body["access_token"]


def _fetch_json(host: str, repo: str, kind: str, digest: str, accept: str) -> dict:
    url = f"https://{host}/v2/{repo}/{kind}/{digest}"
    try:
        with _get(url, accept) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        with _get(url, accept, _token(e.headers.get("WWW-Authenticate", ""))) as r:
            return json.load(r)


def fetch_manifest(host: str, repo: str, digest: str) -> dict:
    return _fetch_json(host, repo, "manifests", digest, ACCEPT)


def fetch_config(host: str, repo: str, digest: str) -> dict:
    return _fetch_json(host, repo, "blobs", digest, "application/json")


def diff_base(env: dict) -> str:
    """The MR's diff base, else the previous pushed commit, else HEAD~1."""
    before = env.get("CI_COMMIT_BEFORE_SHA", "")
    if env.get("CI_MERGE_REQUEST_DIFF_BASE_SHA"):
        return env["CI_MERGE_REQUEST_DIFF_BASE_SHA"]
    return before if before.strip("0") else "HEAD~1"


def report(result: Result, n_refs: int, base: str) -> tuple[list[str], int]:
    """The job log and exit code: red only when a digest failed."""
    lines = [f"skip  {s}" for s in result.skipped] + [f"FAIL  {f}" for f in result.failures]
    lines.append(f"image-platforms: {n_refs} added digest(s) since {base[:12]}: {result.passed} ok, "
                 f"{len(result.skipped)} skipped, {len(result.failures)} failed")
    return lines, 1 if result.failures else 0


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else diff_base(dict(os.environ))
    diff = subprocess.run(["git", "diff", "-U0", base, "HEAD", "--", "*.yml", "*.yaml"],
                          capture_output=True, text=True, check=True).stdout
    refs = added_refs(diff)
    lines, rc = report(check(refs, fetch_manifest, fetch_config), len(refs), base)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
