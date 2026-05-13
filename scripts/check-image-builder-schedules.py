#!/usr/bin/env python3
"""
Lint: every GitLab project under the `mdapi/` group that builds a container
image (i.e. has both `Dockerfile` and `.gitlab-ci.yml` at repo root, plus a
`buildkit` job in the CI) must have exactly one active `pipeline_schedule`
firing every 4 hours, and no two image-builder projects may share the same
cron string.

Run from the repo root:
    python3 scripts/check-image-builder-schedules.py

Exits non-zero with a punch-list when coverage or staggering is wrong.

Pre-reqs:
- `glab` configured for gitlab.mdapi.ch (we use its API token)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from typing import Optional

GITLAB_HOST = "gitlab.mdapi.ch"
GROUP = "mdapi"

# Projects deliberately excluded from the 4-hourly cadence (heavy builds
# only run on demand, e.g. GitLab EE images that track upstream releases).
EXCLUDE = {
    "gitlab-sidekiq-ee",
    "gitlab-webservice-ee",
}

# Cron patterns that mean "every 4 hours". We accept both `*/4` (hour-base 0)
# and `1-23/4`, `2-23/4`, `3-23/4` (offset bases).
FOURHR_HOUR_PATTERNS = {"*/4", "0-23/4", "1-23/4", "2-23/4", "3-23/4"}


def glab_api(path: str) -> object:
    """Call glab api and parse JSON. Bubble up errors."""
    r = subprocess.run(
        ["glab", "api", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def list_group_projects() -> list[dict]:
    """All projects in the mdapi group (paginates)."""
    out = []
    page = 1
    while True:
        # Don't use simple=true — we need the `archived` flag.
        chunk = glab_api(f"groups/{GROUP}/projects?per_page=100&page={page}")
        if not chunk:
            break
        out.extend(chunk)
        page += 1
    return out


def fetch_file(project_path: str, ref: str, file: str) -> Optional[str]:
    """Read a file from a project's default branch via GitLab raw API."""
    enc_proj = project_path.replace("/", "%2F")
    enc_file = file.replace("/", "%2F")
    try:
        r = subprocess.run(
            ["glab", "api", f"projects/{enc_proj}/repository/files/{enc_file}/raw?ref={ref}"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout
    except subprocess.CalledProcessError:
        return None


def is_image_builder(project: dict) -> bool:
    """Heuristic: has Dockerfile + .gitlab-ci.yml at repo root."""
    if not project.get("default_branch"):
        return False
    df = fetch_file(project["path_with_namespace"], project["default_branch"], "Dockerfile")
    if df is None:
        return False
    ci = fetch_file(project["path_with_namespace"], project["default_branch"], ".gitlab-ci.yml")
    return ci is not None


def list_schedules(project_id: int) -> list[dict]:
    return glab_api(f"projects/{project_id}/pipeline_schedules") or []


def is_fourhr_cron(cron: str) -> bool:
    """Match `<M> <hour-pattern> * * *` where hour-pattern is one of the
    recognized 4-hourly forms."""
    m = re.match(r"^\s*(\S+)\s+(\S+)\s+\*\s+\*\s+\*\s*$", cron or "")
    if not m:
        return False
    minute, hour = m.group(1), m.group(2)
    if hour not in FOURHR_HOUR_PATTERNS:
        return False
    # Minute must be a single integer 0-59 (no */N, no ranges).
    if not re.fullmatch(r"\d{1,2}", minute):
        return False
    return 0 <= int(minute) <= 59


def main() -> int:
    projects = list_group_projects()

    builders = []
    print(f"Scanning {len(projects)} projects in group {GROUP}/ ...", file=sys.stderr)
    for p in projects:
        name = p["path"]
        if name in EXCLUDE:
            continue
        if p.get("archived"):
            # Archived projects can't run schedules; GitLab also returns 403
            # on any schedule mutation, so they'd just generate noise.
            continue
        if is_image_builder(p):
            builders.append(p)
    print(f"Found {len(builders)} image-builder projects.", file=sys.stderr)

    missing: list[str] = []
    wrong_cadence: list[tuple[str, list[str]]] = []  # name, crons
    cron_owners: dict[str, list[str]] = defaultdict(list)  # cron -> projects

    for p in builders:
        scheds = [s for s in list_schedules(p["id"]) if s.get("active")]
        if not scheds:
            missing.append(p["path"])
            continue
        fourhr = [s["cron"] for s in scheds if is_fourhr_cron(s["cron"])]
        if not fourhr:
            wrong_cadence.append((p["path"], [s["cron"] for s in scheds]))
            continue
        for cron in fourhr:
            cron_owners[cron].append(p["path"])

    duplicates = {c: ps for c, ps in cron_owners.items() if len(ps) > 1}

    ok = not (missing or wrong_cadence or duplicates)
    if missing:
        print("\nMISSING schedule (image-builder with no active 4h schedule):")
        for n in missing:
            print(f"  - {n}")
    if wrong_cadence:
        print("\nWRONG cadence (active schedule(s) not matching `<M> */4 * * *` or `<M> X-23/4 * * *`):")
        for n, crons in wrong_cadence:
            print(f"  - {n}: {crons}")
    if duplicates:
        print("\nDUPLICATE cron strings (two projects collide on the same slot):")
        for c, ps in duplicates.items():
            print(f"  - {c!r}: {ps}")
    if ok:
        print(f"\nOK — {len(builders)} image-builder projects, all on 4h cadence, no collisions.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
