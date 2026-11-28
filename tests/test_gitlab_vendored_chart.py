"""Guards for the vendored GitLab chart (gitlab/charts/, infra-h0xb).

WHY THIS FILE EXISTS
The core GitLab release used to be the one thing on this cluster with no automated
patch path: hand `helm upgrade` from ~/bootstrap/gitlab-values.yaml, outside Fleet and
outside Renovate. infra-h0xb brought it into Fleet -- and it could not be done the
ordinary way, because Fleet EMBEDS a chart into the Bundle it writes to etcd and the
upstream chart makes a 2,079,280 B bundle against a 1400 KiB gate. So the chart is
vendored under gitlab/charts/ and trimmed to the subcharts this estate renders.

That trade buys a Fleet-managed release and costs an upstream tree inside this repo
(361 files, 209 of them YAML). These tests are what keeps the cost from becoming a
hole:

  * the tree stays OUT of linting, schema validation and Renovate scanning (a lost
    `.vendored` marker would silently pull those 209 YAML files into all three -- as
    WARNINGS, so CI would stay green)
  * the tree stays free of upstream agent-instruction files, which is the classic way
    a vendored directory drags someone else's instructions into a fresh context
  * the version in gitlab/fleet.yaml, the version the tree actually is, and the marker
    can never drift apart -- a mismatch means Fleet installs something other than the
    tree this repo reviewed

Read gitlab/fleet.yaml and tools/vendor-gitlab-chart.py before changing any of it.

⚠️ Discovery here walks the FILESYSTEM rather than `git ls-files` (the repo's usual
rule, see fleetlib). That is deliberate: the hygiene bans below must bite at the moment
a re-vendor drops a file, before anyone stages it.
"""

import json
import re
import unittest
from pathlib import Path

import fleetlib
import yaml

VENDOR_DIR = Path("gitlab/charts")
CHART_DIR = VENDOR_DIR / "gitlab"
MARKER = VENDOR_DIR / ".vendored"

# Upstream ships these; none may reach this repo. CLAUDE.md/AGENTS.md are the ones
# that matter most: an upstream instructions file that lands in a checked-out tree is
# read by the next agent that works in it, with no signal that it is not ours.
BANNED_NAMES = ("CLAUDE.md", "AGENTS.md", ".gitlab-ci.yml")
BANNED_PREFIXES = ("CONTRIBUTING", "CHANGELOG")
BANNED_DIRS = (".gitlab", ".github", ".circleci")

# Inline-secret guard for the vendored tree.
#
# The tree is re-copied WHOLESALE by tools/vendor-gitlab-chart.py on every version
# bump, so nothing in it is ever reviewed line by line the way estate manifests are.
# The repo-wide secret-scan (gitleaks) and public-mirror GATE 3 do cover it -- and
# both passed on the 2026-10-06 merge -- but they are external scanners whose config
# can drift, and neither knows which directories are vendored. This asserts the
# narrow shapes directly, so a re-vendor cannot quietly import key material.
#
# ⚠️ HIGH-SIGNAL ONLY, and that is the whole design. `password`, `secretKey` and
# `privateKey` appear ~60 times in this tree as ordinary chart references
# (privateKeySecretRef, secretKeyRef, a commented `# privateKeyFile:`, schema
# defaults). Gating on those would fail on the first real bump and then be deleted,
# which is worse than no guard at all. Every pattern below has no innocent reading.
#
# ⚠️ Anchored on a PREFIX or a BEGIN header, never on entropy: a bare 40-char hex
# string is indistinguishable from a commit SHA or a checksum, so entropy-hunting
# here would produce mystery failures on ordinary chart text.
#
# Where a pattern has a variable body it is captured as group 1, because the prefix
# itself carries enough distinct characters to defeat the placeholder check below.
SECRET_PATTERNS = (
    # Any PEM flavour (RSA/EC/DSA/OPENSSH/PGP); the header alone is conclusive.
    (r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", "PEM private key block"),
    # Fixed-prefix tokens. The prefix is what makes a regex match safe here.
    (r"\bglpat-([A-Za-z0-9_\-]{20,})", "GitLab personal access token"),
    (r"\bglrt-([A-Za-z0-9_\-]{20,})", "GitLab runner authentication token"),
    (r"\bgh[pousr]_([A-Za-z0-9]{36,})", "GitHub token"),
    (r"\bxox[baprs]-([A-Za-z0-9-]{10,})", "Slack token"),
    (r"\bAKIA([0-9A-Z]{16})\b", "AWS access key id"),
    (r"\bage1([0-9a-z]{50,})", "age secret key"),
)

# Upstream ships documentation placeholders that satisfy the patterns above, and
# tolerating them is what this constant exists for. Measured, not hypothetical:
# gitlab-runner/values.yaml spells a runner token, in its commented `token =` line,
# as the `glrt-` prefix followed by twenty `x` characters. A real token is drawn from
# a 64-symbol alphabet and never repeats one character 20 times, so requiring three
# distinct characters in the BODY separates the two without a general entropy
# heuristic.
#
# ⚠️ The placeholder is described here rather than quoted on purpose: written out it
# would itself be a contiguous token-shaped string in a repo that publishes to a
# public mirror. Same reason the test samples below are concatenated.
#
# ⚠️ The scan deliberately does NOT skip comments. A commented-out line is inert YAML
# but it is exactly where someone pastes a real token "temporarily", so that hole
# would be the wrong shape.
PLACEHOLDER_MIN_DISTINCT = 3


def secret_findings(text: str) -> list[str]:
    """Descriptions of every secret-shaped pattern in `text` (empty == clean)."""
    found = []
    for pat, what in SECRET_PATTERNS:
        for m in re.finditer(pat, text):
            body = m.group(1) if m.re.groups else m.group(0)
            if len(set(body)) < PLACEHOLDER_MIN_DISTINCT:
                continue                      # e.g. glrt-xxxx…, a documentation stub
            found.append(what)
            break
    return found


def marker_fields() -> dict:
    fields = {}
    if not MARKER.is_file():
        return fields
    for line in MARKER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\w+):\s*(\S+)", line)
        if m:
            fields[m.group(1)] = m.group(2)
    return fields


class VendoredChartHygieneTest(unittest.TestCase):
    def test_the_vendored_tree_is_excluded_from_every_sweep(self):
        """The `.vendored` marker is what keeps 209 upstream files out of the gates.

        Losing it fails nothing loudly -- lint.py would just take 209 parse-error
        WARNINGS, validate-manifests.py would count 209 more unparseable files, and
        the Renovate-reachability test would start demanding updaters for upstream
        images this estate does not deploy. All three stay green. So it is asserted
        here instead.
        """
        self.assertTrue(MARKER.is_file(),
                        f"{MARKER} is gone -- every sweep in the repo will now treat "
                        f"the vendored chart as estate manifests")
        self.assertIn(str(VENDOR_DIR), fleetlib.vendored_dirs(),
                      "gitlab/charts/ is not reported as vendored")

        leaked = [f for f in fleetlib.tracked_files()
                  if f.startswith(str(VENDOR_DIR) + "/") and f.endswith((".yaml", ".yml"))]
        self.assertNotEqual([], leaked, "the vendored tree contributes no tracked YAML "
                                        "-- this test would prove nothing")
        self.assertEqual([], [f for f in fleetlib.tracked_yaml()
                              if f.startswith(str(VENDOR_DIR) + "/")],
                         "vendored YAML is still reaching tracked_yaml()")

        self.assertEqual([], [d for d, _ in fleetlib.bundles() if d.startswith(str(VENDOR_DIR))],
                         "a bundle marker inside the vendored tree is being read as a bundle")

    def test_no_upstream_agent_or_ci_files(self):
        found = []
        for p in sorted(CHART_DIR.parent.rglob("*")):
            rel = p.relative_to(CHART_DIR.parent).as_posix()
            if p.is_dir() and p.name in BANNED_DIRS:
                found.append(rel + "/")
            elif p.is_file() and (p.name in BANNED_NAMES
                                  or p.name.startswith(BANNED_PREFIXES)):
                found.append(rel)
        self.assertEqual([], found,
                         "upstream files that must be stripped by "
                         "tools/vendor-gitlab-chart.py: " + ", ".join(found))

    def test_no_stray_fleet_or_kustomize_files(self):
        """Fleet discovers nothing by itself (see fleet-paths.txt), but a stray
        fleet.yaml or kustomization.yaml inside the tree is still a bundle Fleet
        would render if the path were ever added, and a patch kustomize would apply.
        Neither is something upstream ships on purpose."""
        stray = [p.relative_to(CHART_DIR.parent).as_posix()
                 for p in CHART_DIR.parent.rglob("*")
                 if p.is_file() and p.name in
                 ("fleet.yaml", "fleet.yml", "kustomization.yaml", "kustomization.yml")]
        self.assertEqual([], stray, "unexpected bundle/kustomize files: " + ", ".join(stray))

    def test_renovate_ignores_every_vendored_tree(self):
        """Renovate must be told to skip the vendored tree, and nothing else tells it.

        The kubernetes manager's managerFilePatterns is `/\\.ya?ml$/` — every YAML in
        the repo — and helm-values' is `/(^|/)values\\.ya?ml$/`, which the vendored
        tree has one of. So without this entry those 209 files are Renovate's,
        upstream values.yaml included, and it would open bump MRs against a tree the
        next re-vendor overwrites (tools/vendor-gitlab-chart.py deletes and recopies
        the whole directory). tests/test_every_pinned_image_is_renovate_reachable.py
        cannot catch it: that test reads fleetlib.tracked_yaml(), which already
        excludes the marker's subtree, so the config entry itself is unverified there.

        Derived from the marker rather than naming gitlab/charts/, so vendoring a
        second chart cannot forget this.
        """
        cfg = json.loads((fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8"))

        def covers(entry: str, d: str) -> bool:
            """`<dir>`, `<dir>/` and `<dir>/**` all ignore the directory."""
            return entry.split("/**")[0].rstrip("/") == d

        ignored = cfg.get("ignorePaths") or []
        self.assertNotEqual([], fleetlib.vendored_dirs(), "no vendored tree to check")
        for d in fleetlib.vendored_dirs():
            self.assertTrue(any(covers(e, d) for e in ignored),
                            f"renovate.json ignorePaths does not cover the vendored tree "
                            f"{d}/ -- every Renovate manager will scan it (found {ignored})")


class VendoredChartVersionTest(unittest.TestCase):
    """The three places the version lives must agree.

    A mismatch is not cosmetic: gitlab/fleet.yaml is what Fleet installs, the marker is
    the provenance record, and Chart.yaml is the tree on disk. If fleet.yaml's pin is
    ahead of the tree, Fleet installs the OLD tree while everything reads as the new
    version -- the exact silent skew that a hand-run `helm upgrade` would have.
    """

    def test_fleet_pin_marker_and_chart_agree(self):
        self.assertTrue(MARKER.is_file(), f"{MARKER} is missing")
        spec = fleetlib.bundle_spec("gitlab/fleet.yaml")
        pinned = str((spec.get("helm") or {}).get("version") or "").strip()

        chart_yaml = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))
        in_tree = str(chart_yaml.get("version") or "").strip()

        marked = marker_fields().get("version", "")

        self.assertRegex(pinned, r"^\d+\.\d+\.\d+$", "gitlab/fleet.yaml helm.version")
        self.assertEqual(pinned, in_tree,
                         "gitlab/fleet.yaml pins a chart version the vendored tree is not "
                         "-- re-run tools/vendor-gitlab-chart.py")
        self.assertEqual(pinned, marked,
                         "gitlab/charts/.vendored disagrees with the pin -- the marker is "
                         "written by the vendor script, so re-vendor rather than edit it")

    def test_chart_dependencies_match_the_subcharts_on_disk(self):
        """`helm template` HARD-FAILS on a Chart.yaml dependency with no directory, and
        SILENTLY IGNORES a directory with no dependency entry. So a half-finished
        re-vendor either breaks every render or quietly drops a subchart's resources.
        This names the exact drift before either happens."""
        chart_yaml = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))
        declared = {d["name"] for d in chart_yaml.get("dependencies") or []}
        on_disk = {p.name for p in (CHART_DIR / "charts").iterdir() if p.is_dir()}

        self.assertEqual(sorted(declared), sorted(on_disk),
                         "Chart.yaml dependencies and charts/ have drifted apart -- "
                         "re-run tools/vendor-gitlab-chart.py")
        self.assertNotEqual(set(), on_disk, "the vendored chart declares no subcharts at all")


class VendoredChartSecretTest(unittest.TestCase):
    """No key material may ride in with a re-vendored upstream tree.

    Read SECRET_PATTERNS for why the pattern set is small and prefix-anchored.
    """

    def test_the_detector_fires_on_real_shapes_and_stays_quiet_on_chart_noise(self):
        """Prove the guard is neither vacuous nor a tripwire on ordinary templates.

        Both halves matter. A pattern set that never fires is decoration, and one
        that fires on `secretKeyRef:` gets deleted after the first bump.

        ⚠️ The samples are CONCATENATED, not written whole, and that is load-bearing.
        A contiguous BEGIN header, or `glpat-` followed by 20 token characters, would
        itself match gitleaks in the repo-wide secret-scan job AND in public-mirror
        GATE 3 — so this guard would fail the very pipeline it exists to protect.
        Do not "simplify" these into single literals.
        """
        self.assertEqual(["PEM private key block"],
                         secret_findings("-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE"))
        self.assertEqual(["GitLab personal access token"],
                         secret_findings("token: glpat-" + "A1b2C3d4E5f6G7h8I9j0"))
        self.assertEqual(["AWS access key id"],
                         secret_findings("aws_access_key_id = AKIA" + "IOSFODNN7EXAMPLE"))

        # Upstream's documentation stubs must NOT fire. This is the measured case the
        # first version of this guard failed on: gitlab-runner/values.yaml line 582.
        for stub in ('#     token = "glrt-' + "x" * 20 + '"',
                     '#   token = "glpat-' + "x" * 20 + '"'):
            self.assertEqual([], secret_findings(stub), f"placeholder fired: {stub!r}")

        for innocent in ("privateKeySecretRef:", "secretKeyRef:", "password: changeme",
                         "privateKeyFile: /etc/gitlab/ssl/gitlab.example.com.key",
                         "sha256: " + "a" * 64, "revision: " + "0" * 40):
            self.assertEqual([], secret_findings(innocent),
                             f"false positive on ordinary chart text {innocent!r}")

    def test_no_inline_secrets_in_the_vendored_tree(self):
        """Walks every tracked file under every vendored dir (marker-derived)."""
        root = fleetlib.repo_root()
        vendored = [f for f in fleetlib.tracked_files() if fleetlib.is_vendored(f)]
        self.assertNotEqual([], vendored,
                            "no tracked files under a vendored dir -- this test would "
                            "prove nothing (is the .vendored marker still there?)")

        findings = []
        for rel in vendored:
            try:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:                      # unreadable is itself a finding
                findings.append(f"{rel}: cannot read ({exc})")
                continue
            findings += [f"{rel}: {what}" for what in secret_findings(text)]

        self.assertEqual([], findings,
                         "secret-shaped material under a vendored tree -- gitlab/charts/ "
                         "is upstream content, re-copied wholesale by "
                         "tools/vendor-gitlab-chart.py on every bump, so nothing there is "
                         "reviewed line by line: " + "; ".join(findings))


if __name__ == "__main__":
    unittest.main()
