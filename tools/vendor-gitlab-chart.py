#!/usr/bin/env python3
"""Vendor the upstream GitLab Helm chart into gitlab/charts/gitlab/.

WHY THIS EXISTS (infra-h0xb)
The core GitLab release lives in ns `bootstrap` and was hand-installed with
`helm upgrade` from ~/bootstrap/gitlab-values.yaml -- outside Fleet and outside
Renovate, so a GitLab CVE had no automated patch path. A Fleet bundle CANNOT
reference the chart the normal way:

    repo: https://charts.gitlab.io/ ; chart: gitlab

Fleet embeds the chart's files into the Bundle it writes to etcd. Measured
2026-10-06 with fleet v0.16.1 (`tools/ci/bundle-gate.py`'s exact invocation):

    remote chart, untrimmed ...... 2,079,280 B   > the 1400 KiB FAIL mark
    vendored, trimmed, stripped ..... 494,814 B  < the 800 KiB warn mark

(Treat the second figure as a margin, not a constant -- but not for the reason
this said until 2026-10-06. The bundle does NOT embed fleet.yaml or values.yaml;
only the CHART's files are embedded, each gzipped then base64'd (verified on the
live Bundle: spec.resources[].content starts with the gzip magic H4sIAAAAAAAA/).
A comment in either of those two files therefore moves the figure by ZERO bytes
-- measured: +3,240 B in gitlab/fleet.yaml -> 494,814 unchanged, +1,360 B in
gitlab/values.yaml -> 494,814 unchanged, while `keepResources: true` added at
column 0 (22 B of real spec) -> 494,836. What actually moves it is this tree:
re-vendoring is what changes the number. tools/ci/bundle-gate.py measures it.)

So the chart is vendored here, with the nine subcharts this estate does not use
removed (upstream 10.4.1 declares 14 dependencies over 13 shipped directories;
KEEP_SUBCHARTS is the other four). The trim is not cosmetic -- it is the whole
reason the bundle fits.

⛔ KEEPING THE TRIM HONEST. `helm template` HARD-FAILS if Chart.yaml lists a
dependency whose directory is absent from charts/:
    found in Chart.yaml, but missing in charts/ directory: cert-manager, ...
so the dropped entries are removed from Chart.yaml and Chart.lock is deleted.
A re-enable of a dropped subchart is therefore a silent no-op until it is added
to KEEP_SUBCHARTS and re-vendored. That is accepted: this release sets every one
of them disabled in values.yaml, and a chart version bump re-vendors from scratch.

⚠️ Every version bump re-runs this script, which rewrites every file under
gitlab/charts/gitlab/. Read gitlab/fleet.yaml for how a Renovate bump drives it.

⭐ IT ALSO REWRITES THE PIN. `gitlab/fleet.yaml`'s helm.version and the tree under
charts/ are two halves of ONE fact — "the chart this bundle installs is <V>" — so this
script writes both, and a Renovate bump is completed by this one command rather than by
this command plus a hand edit. `tests/test_gitlab_vendored_chart.py` fails the build
while the two disagree, which is what makes an un-re-vendored Renovate MR unmergeable.

USAGE
    python3 tools/vendor-gitlab-chart.py 10.4.1
    python3 tools/vendor-gitlab-chart.py --from-tgz /path/to/gitlab-10.4.1.tgz 10.4.1

Needs `helm` on PATH for the download path (offline re-vendor: use --from-tgz).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHART_PARENT = REPO / "gitlab" / "charts"
DEST = CHART_PARENT / "gitlab"
MARKER = CHART_PARENT / ".vendored"
FLEET_YAML = REPO / "gitlab" / "fleet.yaml"

# The one line in gitlab/fleet.yaml this script owns. Anchored to the helm block's
# two-space indent AND to a well-formed X.Y.Z, and required to match EXACTLY ONCE:
# a loose `version:` match could silently rewrite a different key, and no match at all
# would leave the pin and the tree disagreeing -- the exact skew
# tests/test_gitlab_vendored_chart.py exists to catch, arriving through the tool that
# is supposed to prevent it. So both cases are hard failures here.
PIN_RE = re.compile(r"(?m)^(  version: )(\d+\.\d+\.\d+)$")

CHART_NAME = "gitlab"
CHART_REPO = "https://charts.gitlab.io/"

# The subcharts this release actually renders. Everything else upstream ships is
# dropped; keep this in step with gitlab/values.yaml, which disables the rest.
KEEP_SUBCHARTS = ("gitlab", "certmanager-issuer", "registry", "gitlab-runner")

# Upstream ships developer-facing files that must never land in this repo.
# ⛔ CLAUDE.md/AGENTS.md are the dangerous ones: a vendored tree is the classic
# way an upstream agent-instructions file drags instructions into a new context.
# tests/test_gitlab_vendored_chart.py fails the build if any of these reappear.
STRIP_NAMES = ("CLAUDE.md", "AGENTS.md")
STRIP_PREFIXES = ("CONTRIBUTING", "CHANGELOG")
STRIP_DIRS = (".gitlab", ".github", ".circleci")
STRIP_FILES = (".gitlab-ci.yml",)

# Provenance marker. Doubles as the machine-readable answer to "which trees are
# vendored?" for lint.py / tests/fleetlib.py, which must not lint or Renovate-scan
# 200 upstream YAML files as if they were estate manifests.
MARKER_TEMPLATE = """\
# Machine-readable provenance for the vendored chart beside this file.
#
# Written by tools/vendor-gitlab-chart.py -- do not edit by hand.
#
# ⛔ The directory this file sits in (gitlab/charts/) holds an UPSTREAM tree.
# lint.py and tests/fleetlib.py use this marker to exclude it: none of the YAML
# under here is an estate manifest, and none of its images is ours to track.
#
# The chart's Chart.yaml is the source of truth for the version; this file says
# where it came from. tools/vendor-gitlab-chart.py rewrites both together.
chart: {chart}
version: {version}
source: {repo}
sha256: {sha256}
"""

# Chart.yaml's `dependencies:` is a block sequence at indent 0 ("- name: x"), so the
# block ends at the first indent-0 line that is not a list item. Line-based on
# purpose: everything outside the dependency block must stay byte-identical to
# upstream, so a re-vendor diff shows only real upstream changes.
def _dep_field(item: list[str], key: str) -> str | None:
    for line in item:
        body = line[2:] if line.startswith("- ") else line
        m = re.match(r"\s*%s:\s*(\S+)" % key, body)
        if m:
            return m.group(1)
    return None


def trim_chart_dependencies(chart_dir: Path) -> tuple[list[str], list[str]]:
    path = chart_dir / "Chart.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")

    start = next((i for i, l in enumerate(lines) if l.startswith("dependencies:")), None)
    if start is None:
        sys.exit("Chart.yaml has no top-level dependencies: block -- upstream layout changed")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and not lines[i].startswith(("- ", " "))), len(lines))

    items: list[list[str]] = []
    cur: list[str] = []
    for line in lines[start + 1:end]:
        if line.startswith("- "):
            if cur:
                items.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        items.append(cur)

    kept, dropped = [], []
    for item in items:
        name = _dep_field(item, "name")
        if not name:
            sys.exit("unparsable dependency entry: %r" % item)
        (kept if name in KEEP_SUBCHARTS else dropped).append((name, item))

    path.write_text(
        "\n".join(lines[:start + 1] + [l for _, it in kept for l in it] + lines[end:]),
        encoding="utf-8")
    return [n for n, _ in kept], [n for n, _ in dropped]


def strip_junk(root: Path) -> list[str]:
    removed = []
    for p in sorted(root.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            if p.name in STRIP_DIRS:
                removed.append(rel + "/")
                shutil.rmtree(p)
            continue
        if (p.name in STRIP_NAMES or p.name in STRIP_FILES
                or p.name.startswith(STRIP_PREFIXES)):
            removed.append(rel)
            p.unlink()
    return sorted(removed)


def trim_subcharts(chart_dir: Path) -> list[str]:
    subcharts = chart_dir / "charts"
    dropped = []
    for d in sorted(subcharts.iterdir()):
        if d.name not in KEEP_SUBCHARTS:
            dropped.append(d.name)
            shutil.rmtree(d)
    return dropped


def fetch(version: str, work: Path) -> Path:
    subprocess.run(
        ["helm", "pull", "%s/%s" % ("gitlab", CHART_NAME), "--version", version,
         "--destination", str(work)],
        check=True)
    tgzs = list(work.glob("%s-*.tgz" % CHART_NAME))
    if len(tgzs) != 1:
        sys.exit("expected exactly one pulled tgz, found %r" % tgzs)
    return tgzs[0]


def rewrite_fleet_pin(version: str) -> str:
    """Point gitlab/fleet.yaml's helm.version at `version`; return the old value.

    Line-based and single-substitution, deliberately: everything else in that file --
    including the `# renovate:` line the custom.regex manager anchors on -- must come
    back byte-identical, so a re-vendor diff shows the pin and nothing else.
    """
    text = FLEET_YAML.read_text(encoding="utf-8")
    matches = PIN_RE.findall(text)
    if len(matches) != 1:
        # os.path.relpath, not Path.relative_to: the latter raises ValueError instead of
        # printing the error below whenever the path is not under REPO, which would turn
        # a clear refusal into a traceback.
        sys.exit("expected exactly one '  version: X.Y.Z' line in %s, found %d -- "
                 "refusing to guess which one is the helm pin"
                 % (os.path.relpath(FLEET_YAML, REPO), len(matches)))
    previous = matches[0][1]
    if previous != version:
        FLEET_YAML.write_text(PIN_RE.sub(lambda m: m.group(1) + version, text, count=1),
                              encoding="utf-8")
    return previous


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="upstream chart version, e.g. 10.4.1")
    ap.add_argument("--from-tgz", type=Path,
                    help="use this already-downloaded chart archive instead of pulling")
    ap.add_argument("--keep-going", action="store_true",
                    help="do not fail when the vendored tree is unchanged")
    args = ap.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("version must be X.Y.Z, got %r" % args.version)

    # The extracted tree is UNTRUSTED upstream content. Keep it in its own temp
    # directory and never chdir into it or run anything from inside it.
    with tempfile.TemporaryDirectory(prefix="vendor-gitlab-") as tmp:
        work = Path(tmp)
        tgz = args.from_tgz or fetch(args.version, work)
        if not tgz.is_file():
            sys.exit("no chart archive at %s" % tgz)

        import hashlib
        sha = hashlib.sha256(tgz.read_bytes()).hexdigest()

        with tarfile.open(tgz) as tf:
            tf.extractall(work / "x", filter="data")
        src = work / "x" / CHART_NAME
        if not src.is_dir():
            sys.exit("%s does not contain a %s/ directory" % (tgz, CHART_NAME))

        chart_version = re.search(r"^version:\s*(\S+)", (src / "Chart.yaml").read_text(
            encoding="utf-8"), re.M)
        if not chart_version or chart_version.group(1) != args.version:
            sys.exit("archive is %s but version argument is %s"
                     % (chart_version and chart_version.group(1), args.version))

        stripped = strip_junk(src)
        dropped = trim_subcharts(src)
        kept, dep_dropped = trim_chart_dependencies(src)
        lock = src / "Chart.lock"
        if lock.exists():
            # A stale lock lists the dropped dependencies and helm refuses to render.
            lock.unlink()
            stripped.append("Chart.lock")

        CHART_PARENT.mkdir(parents=True, exist_ok=True)
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.copytree(src, DEST, symlinks=False)
        MARKER.write_text(MARKER_TEMPLATE.format(
            chart="gitlab/gitlab", version=args.version, repo=CHART_REPO, sha256=sha),
            encoding="utf-8")

        # LAST, and only once the tree it describes is on disk: a run that died above
        # would otherwise leave the pin naming a version the tree is not.
        previous = rewrite_fleet_pin(args.version)

    print("vendored gitlab/gitlab %s -> %s" % (args.version, DEST.relative_to(REPO)))
    print("  subcharts kept:    %s" % ", ".join(kept))
    print("  subcharts dropped: %s" % ", ".join(dropped))
    print("  dependencies dropped from Chart.yaml: %s" % ", ".join(dep_dropped))
    print("  files stripped: %d (%s)" % (len(stripped), ", ".join(stripped[:4]) +
                                         (" ..." if len(stripped) > 4 else "")))
    print("  sha256(archive): %s" % sha)
    print("  gitlab/fleet.yaml helm.version: %s -> %s%s"
          % (previous, args.version, " (unchanged)" if previous == args.version else ""))
    print()
    print("  NEXT: the rendered output must be unchanged -- run"
          " tools/ci/render-bundles.py")
    print("  and diff. Then `git add -A gitlab/` for both halves in ONE commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
