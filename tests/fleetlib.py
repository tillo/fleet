"""Shared helpers for the repo invariant tests.

Discovery is by `git ls-files`, so only TRACKED files are examined -- the same rule CI
uses. A new manifest that is not yet `git add`ed is invisible here; that is correct for
CI (it validates a commit) but is a trap when running locally.

⛔ Bundle markers are BOTH `fleet.yaml` and `fleet.yml`. Fleet reads either, and 32 of
this repo's bundles use `.yml`. Anything that looks at only one spelling silently skips a
fifth of the estate.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

import yaml

BUNDLE_MARKERS = ("fleet.yaml", "fleet.yml")


@functools.lru_cache(maxsize=1)
def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


@functools.lru_cache(maxsize=1)
def tracked_files() -> tuple[str, ...]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo_root(),
                         capture_output=True, text=True, check=True)
    return tuple(f for f in out.stdout.split("\0") if f)


def tracked_yaml() -> list[str]:
    return [f for f in tracked_files() if f.endswith((".yaml", ".yml"))]


def load_docs(rel: str) -> list[dict]:
    """Every mapping document in a tracked YAML file. Unparseable files yield nothing --
    schema validity is validate-manifests.py's job, not ours."""
    try:
        raw = (repo_root() / rel).read_text(encoding="utf-8")
        return [d for d in yaml.safe_load_all(raw) if isinstance(d, dict) and d]
    except (OSError, yaml.YAMLError):
        return []


@functools.lru_cache(maxsize=1)
def bundles() -> tuple[tuple[str, str], ...]:
    """(bundle_dir, marker_path) for every Fleet bundle, recursively.

    fleet.yaml wins if a directory somehow carries both spellings, matching Fleet.
    """
    found: dict[str, str] = {}
    for f in tracked_files():
        if os.path.basename(f) in BUNDLE_MARKERS:
            d = os.path.dirname(f)
            if d not in found or os.path.basename(f) == "fleet.yaml":
                found[d] = f
    return tuple(sorted(found.items()))


def bundle_spec(marker: str) -> dict:
    docs = load_docs(marker)
    return docs[0] if docs else {}


def files_under(bundle_dir: str) -> list[str]:
    """Tracked YAML inside a bundle, excluding the bundle marker itself."""
    prefix = bundle_dir + "/" if bundle_dir else ""
    out = []
    for f in tracked_yaml():
        if not f.startswith(prefix):
            continue
        if os.path.basename(f) in BUNDLE_MARKERS:
            continue
        # a nested bundle owns its own files
        rest = f[len(prefix):]
        if any(os.path.dirname(rest).startswith(os.path.relpath(d, bundle_dir))
               for d, _ in bundles() if d != bundle_dir and d.startswith(prefix)):
            continue
        out.append(f)
    return out
