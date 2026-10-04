"""A pinned image that no Renovate manager can reach is frozen, and nothing says so.

Found four times now, each time by accident, each time fixed one manager at a time:

  2026-07-15  Lane-B closure -- `alpine/k8s` had sat at 1.31.1 for ~a year.
  2026-09-12  helm-values -- 12 of 14 values.yml files were read by NO manager, so a
              `# renovate:` comment in them was decorative.
  2026-09-13  CVE-2026-59822 audit -- `zot.mdapi.ch/mdapi/litellm` was pinned in a file
              the kubernetes manager DOES read, but the package had no opt-in rule, so
              it was never evaluated at all.
  2026-09-22  DIGEST-ONLY refs -- and this one was hidden from THIS FILE, not just from
              Renovate. `repo@sha256:...` with no tag made the old two-value split_ref()
              return tag=None, is_pinned(None) returned False, and the ref was dropped
              from the scan before any gate was evaluated. So the test went green while
              7 pins were unreachable. OpenObserve was the casualty: moved off Keel on
              2026-08-12 with a digest pin, never opted in here, and therefore owned by
              NEITHER lane -- frozen at EE v0.92.0 for six weeks, through v0.92.1,
              v0.92.2 and the entire v1.0.x line, with no MR and no dashboard row.
              ⭐ The lesson is the one this repo keeps relearning: a scan that CANNOT
              match returns the same green as a scan that found nothing. The fix is
              in split_ref(); the remaining 5 digest-only pins are listed below.

The reason it keeps escaping every check is that this failure emits NO signal.
`/renovate-audit` looks for `Package lookup failures` in the dependency dashboard, but
that only exists once Renovate has ATTEMPTED a lookup. A package excluded by
`enabled: false`, or living in a file no `managerFilePatterns` matches, is never
attempted: no MR, no dashboard row, no problem entry, no log line. The bot stays green
and the pin never moves. Per tests/README.md, the second sighting should have become
this file.

Two independent gates have to BOTH pass for a pin to be reachable:

  1. the file matches some enabled manager's `managerFilePatterns` -- the kubernetes
     manager reads ONLY `*-deploy.ya?ml` and `*-ds.ya?ml`, so every StatefulSet,
     CronJob and otherwise-named manifest is invisible even with a perfect rule;
  2. for the kubernetes manager, packageRules[0] is `enabled: false` (Default OFF), so
     the name must appear in a later rule that sets `enabled: true` -- in
     `matchPackageNames` OR `matchDepNames`, which match `packageName` and `depName`
     respectively and diverge under `registryAliases` (see kubernetes_opt_ins).

Everything here is DERIVED from renovate.json rather than restated, so editing the
config moves the test with it. Hardcoding the policy you are changing is how a probe
pages you for being right.

This is a RATCHET: it fails if KNOWN_UNREACHABLE GROWS (a new blind pin) and equally if
it goes STALE (an entry now reachable, or gone), so the debt can only shrink and cannot
be quietly re-hidden. It shipped carrying 28 frozen pins; 27 were paid off on 2026-09-13
by widening the kubernetes manager's file gate to every yaml and opting the real
workloads in. The one that remains is a decision, not an oversight — see the entry.

⚠️ Tag shape matters to what counts here. Three or more numeric components names one
build (`26.7.2`, `1.6.0`) and is a pin. Fewer names a LINE that rolls on its own
(`alpine:3.20`, `python:3.13-slim`, `mongo:7`).

⭐ 2026-10-02 — THE LANE CHANGED, AND SO DID THIS FILE'S MODEL OF IT. Renovate's
kubernetes manager used to be Default OFF (packageRules[0] `enabled: false`), because
Keel owned the floating refs and the two would have fought over the live image. That
whole estate is being moved onto "Lane A with Renovate": the tag keeps tracking, the
DIGEST is what git freezes, and a moved tag arrives as an ordinary MR. So:

  * the Default OFF rule is GONE — a package is Renovate's unless a rule HOLDS it
    (`enabled: false` with no `matchUpdateTypes`, i.e. off for every update type);
  * a ref that carries an explicit `@sha256` is a PIN no matter what its tag looks
    like, so `python:3-alpine@sha256:…` is now in scope. That closes the question the
    paragraph above used to leave open — such an image no longer "moves only when a
    pod happens to be recreated": it moves when a commit says so;
  * the ONE shape still out of reach by construction is a digest with NO TAG
    (`repo@sha256:…`). It names one build, carries no version for Renovate to compare,
    and no manager setting can rescue it — the tag has to come back. Those are
    asserted here explicitly rather than being allowed to fall out of the scan again,
    which is how 7 of them hid until 2026-09-22.

⭐ A note on why the hold list is expressed as a predicate wherever possible: the
recurring failure in this repo is not a wrong decision, it is a LIST nobody remembered
to update. Lane A is therefore defined by tag SHAPE (see the LANE-A PREDICATE rule in
renovate.json), so an image becomes Lane A on the commit that adds it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import unittest

import fleetlib

# Tags that move on their own. These belong to Lane-A (Keel) and are out of scope here;
# `2-alpine`, `16` and `pg18` are major-LINE tags that roll across minors by themselves.
FLOATING_TAGS = {"latest", "stable", "main", "master", "edge", "dev", "nightly", "develop"}
MAJOR_LINE_RE = re.compile(r"v?\d+(-[A-Za-z0-9._-]+)?$")

# `image:` in a manifest, skipping comment lines -- a grep hit is not a use.
IMAGE_RE = re.compile(r"^\s*(?:-\s+)?image:\s*[\"']?([A-Za-z0-9][^\s\"'#]*)[\"']?\s*(?:#.*)?$")

# Not Kubernetes workloads: the GitLab CI manager is not in enabledManagers, so job
# images are a separate question from whether a deployed pin can move.
OUT_OF_SCOPE_FILES = {".gitlab-ci.yml"}

# ⚠️ Scope limit, stated rather than hidden: this scans `image: repo:tag` STRINGS. It does
# not see the Helm object shape (`image: {registry, repository, tag}`) — those are read by
# the helm-values manager in values.ya?ml, but the same shape in any other file is
# invisible to that manager AND to this test. openbao/fleet.yaml below is exactly that
# case and is why the gap is worth naming.
#
# Pins that remain unreachable. SIX entries, and after 2026-10-02 they are TWO kinds
# and only two: (1) a digest with no tag, which no manager setting can reach, and
# (2) openbao's helm-object shape, which the file hand-back keeps away from the
# kubernetes manager. ⭐ Everything else that used to sit here was cleared out by
# Default ON rather than forgiven — it went from 28 to 6 because the class of failure
# this list records (a package nobody opted in) no longer exists. Burn these down too;
# do not add to it.
# Key is (file, image repo) so an ordinary version bump does not invalidate an entry.
KNOWN_UNREACHABLE = {
    # openbao is pinned TWICE in this one file, at the same 2.5.5: as a Helm object
    # (server.image.{registry,repository,tag}) and as a bare string on the unsealer
    # sidecar. Neither is reachable — the fleet manager reads CHART versions, and
    # helm-values only reads values.ya?ml. ⛔ Do not "fix" this by pointing a
    # customManager at the bare string: bumping one occurrence and not the other runs
    # the server and its unsealer on different builds of a SECRETS store. The real fix
    # is a decision — move the values into a real values.yml (helm-values then reads
    # both shapes natively), or extend helm-values to fleet.ya?ml and confirm it parses
    # a nested helm.values block correctly. Left deliberately visible until decided.
    # ⭐ 2026-09-22: this entry SHRANK. It used to cover both openbao pins,
    # because both lived on the hand-pushed `mdapi/openbao` path. The repoint to
    # the quay.io pull-through `openbao/openbao` split them: the bare-string pin
    # in openbao-bootstrap's CronJob is an ordinary `image:` line and is now
    # Renovate-tracked like anything else, so only the HELM-OBJECT shape below
    # is still unreachable. The remaining reason is structural, not an oversight
    # — `server.image.{registry,repository,tag}` in a fleet.yaml is read by the
    # fleet manager (which owns CHART versions, not image strings) and by
    # helm-values (which only reads values.ya?ml).
    # ⛔ Do not "fix" it with a customManager on the bare string: the unsealer
    # sidecar in the same file pins the same image, and bumping one shape alone
    # runs a secrets store and its unsealer on different builds.
    ("openbao/fleet.yaml", "zot.mdapi.ch/openbao/openbao"): "helm-object shape (server.image.{registry,repository,tag}) in a fleet.yaml: the fleet manager reads chart versions and helm-values only reads values.ya?ml",
    # ── The DIGEST-ONLY five, newly VISIBLE on 2026-09-22 ────────────────────────
    # These are not new debt; they have been frozen all along and this scan simply
    # could not see them. They are listed rather than fixed because each needs its
    # own tag decision, and OpenObserve (the pin that prompted this) was fixed
    # rather than forgiven -- see openobserve/o2-sts.yml for the pattern: put the
    # TAG back alongside the digest, then add an `enabled: true` packageRule.
    # ⭐ 2026-10-02: Default ON did half of that fix for free — the `enabled: true`
    # packageRule is no longer needed for ANY of these. What is left is purely the
    # tag decision, which is why they remain: `repo@sha256:…` carries no version for
    # Renovate to compare, so the pin cannot move however the manager is configured.
    # ⭐ Cheapest first: dolt already records its tag in a comment.
    ("backlog/dolt-sts.yml", "dolthub/dolt-sql-server"): "digest-only; its own comment says '= tag 2.2.3 at pin time' and cites openobserve's now-fixed policy, so re-adding :2.2.3 + an opt-in rule closes it",
    ("external-dns-mdapi/external-dns-mdapi.yml", "registry.mdapi.ch/mdapi/external-dns-unbound-webhook"): "digest-only; own-built mdapi image, so the tag to re-add is whatever its CI mints -- confirm against the registry before pinning",
    ("jump/openvpn.yml", "docker.io/flant/ovpn-admin"): "digest-only; upstream tags this image sparsely, so decide a tag line before opting it in",
    ("jump/openvpn.yml", "docker.io/library/alpine"): "digest-only base image used twice in this file (ip_forward init + the openvpn container); a bare alpine LINE tag would roll on its own and is the separate floating-without-Keel question, so this needs the tag decision made deliberately",
    ("macos-vm/macos-vm-deploy.yml", "sickcodes/docker-osx"): "digest-only; upstream publishes moving tags only, so there may be no stable tag to pin -- may legitimately stay here",
}


def renovate_config() -> dict:
    return json.loads((fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8"))


def to_regex(pattern: str) -> re.Pattern:
    """Renovate writes managerFilePatterns as `/<regex>/`; bare strings are prefixes."""
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 1:
        return re.compile(pattern[1:-1])
    return re.compile("^" + re.escape(pattern))


def manager_file_patterns(cfg: dict) -> dict[str, list[re.Pattern]]:
    """Which regexes each enabled manager uses to pick files, straight from the config."""
    out: dict[str, list[re.Pattern]] = {}
    for mgr in cfg.get("enabledManagers", []):
        if mgr == "custom.regex":
            pats = [p for cm in cfg.get("customManagers", [])
                    for p in cm.get("managerFilePatterns", [])]
        else:
            pats = (cfg.get(mgr) or {}).get("managerFilePatterns", [])
        out[mgr] = [to_regex(p) for p in pats]
    return out


def kubernetes_opt_ins(cfg: dict) -> set[str]:
    """Names re-enabled for the kubernetes manager after the Default OFF rule.

    A rule with no matchManagers applies to every manager, so it counts too -- but only
    if it actually sets `enabled: true`; packageRules are last-wins PER FIELD, and a rule
    that merely sets a soak or an allowedVersions does not lift `enabled: false`.

    ⭐ BOTH matchers count, because they match DIFFERENT fields and only one of them is
    stable here. Renovate's own matchers (util/package-rules/{package,dep}-names.js):
    `matchPackageNames` tests `packageName`, `matchDepNames` tests `depName`. For a
    mirror path those diverge the moment `registryAliases` is in play -- it rewrites
    packageName to the upstream repo (zot.mdapi.ch/berriai/litellm -> ghcr.io/berriai/
    litellm) while depName stays the mirror. So a rule written with matchPackageNames
    against the mirror STOPS MATCHING, the Default OFF silently reasserts itself, and
    the pin freezes with this test still green -- the identical blind spot to the
    `enabled: false` case above, one layer down. Reading only matchPackageNames here
    would also make this test bless that broken rule. Verified against Renovate 41.
    """
    names: set[str] = set()
    for rule in cfg.get("packageRules", []):
        mgrs = rule.get("matchManagers")
        if rule.get("enabled") is True and (mgrs is None or "kubernetes" in mgrs):
            names.update(rule.get("matchDepNames", []))
            # ⛔ An aliased-away matchPackageNames entry is NOT an opt-in. It can never
            # match, so counting it would make this test bless the frozen pin twice.
            names.update(n for n in rule.get("matchPackageNames", [])
                         if aliased_name(n, cfg) == n)
    return names


def kubernetes_default_on(cfg: dict) -> bool:
    """Is the kubernetes manager ON for everything, unless a rule holds a package?

    True since 2026-10-02, when the Keel-era `Default OFF` rule was deleted. Both
    regimes are modelled rather than one being assumed: the config is read, not
    hardcoded, so reintroducing a manager-wide `enabled: false` flips this back and
    the opt-in path below takes over again. A rule that is scoped to particular
    packages, files or update types is a HOLD, not a manager-wide switch.
    """
    for rule in cfg.get("packageRules", []):
        if rule.get("enabled") is not False:
            continue
        mgrs = rule.get("matchManagers")
        if mgrs is not None and "kubernetes" not in mgrs:
            continue
        if (rule.get("matchPackageNames") or rule.get("matchDepNames")
                or rule.get("matchFileNames") or rule.get("matchUpdateTypes")):
            continue                      # scoped -> a hold, not a manager-wide switch
        return False
    return True


def held_packages(cfg: dict) -> set[str]:
    """Packages a rule switches off for EVERY update type (a full hold).

    ⛔ A hold that names a mirrored path is not a hold — the same `registryAliases`
    trap as an opt-in: the rule can never match, so the package stays live. Only
    names that survive aliasing count, so a broken hold is reported as reachable
    rather than blessed.

    ⚠️ A rule with `matchUpdateTypes` is deliberately NOT a hold. renovate.json uses
    exactly that shape for the LANE-A PREDICATE (floating tags get no major/minor/
    patch, but `digest` still flows), and a package held only that way is very much
    reachable — by digest, which is the whole point of Lane A.
    """
    out: set[str] = set()
    for rule in cfg.get("packageRules", []):
        if rule.get("enabled") is not False:
            continue
        mgrs = rule.get("matchManagers")
        if mgrs is not None and "kubernetes" not in mgrs:
            continue
        if rule.get("matchUpdateTypes") or rule.get("matchFileNames"):
            continue
        out.update(n for n in rule.get("matchDepNames", []) if aliased_name(n, cfg) == n)
        out.update(n for n in rule.get("matchPackageNames", []) if aliased_name(n, cfg) == n)
    return out


def registry_aliases(cfg: dict) -> dict[str, str]:
    """The kubernetes manager's registryAliases: mirror prefix -> upstream prefix."""
    return (cfg.get("kubernetes") or {}).get("registryAliases") or {}


def aliased_name(name: str, cfg: dict) -> str:
    """The `packageName` this depName resolves to -- what matchPackageNames sees.

    Renovate matches the alias on a raw name prefix (util/package-rules -> registryAliases
    in datasource/docker), so `zot.mdapi.ch/berriai` rewrites
    `zot.mdapi.ch/berriai/litellm` to `ghcr.io/berriai/litellm`.
    """
    for prefix, replacement in registry_aliases(cfg).items():
        if name == prefix or name.startswith(prefix.rstrip("/") + "/"):
            return replacement + name[len(prefix):]
    return name


def manager_disabled_files(cfg: dict) -> dict[str, list[str]]:
    """Files a manager is switched OFF for by a `matchFileNames` + `enabled: false` rule.

    The kubernetes manager reads every yaml now, so values.ya?ml and fleet.ya?ml are
    handed back to helm-values / fleet this way. Ignoring that here would let the test
    call a pin reachable that Renovate has actually been told to skip.
    """
    out: dict[str, list[str]] = {}
    for rule in cfg.get("packageRules", []):
        if rule.get("enabled") is not False:
            continue
        globs = rule.get("matchFileNames")
        if not globs:
            continue
        for mgr in rule.get("matchManagers") or list(cfg.get("enabledManagers", [])):
            out.setdefault(mgr, []).extend(globs)
    return out


def file_matches_glob(rel: str, glob_pat: str) -> bool:
    """Minimatch subset: `**/` may match zero directories, so compare the basename."""
    if glob_pat.startswith("**/"):
        return os.path.basename(rel) == glob_pat[3:]
    return fnmatch.fnmatch(rel, glob_pat)


def spellings(repo: str) -> set[str]:
    """Renovate normalises Docker Hub names; compare every spelling of the same image.

    A manifest may write `docker.io/aquasec/kube-bench` or `aquasec/kube-bench`, and an
    official image as `caddy` or `library/caddy`. Matching only the literal string makes
    a perfectly good packageRule look absent.
    """
    out = {repo}
    if repo.startswith("docker.io/"):
        out.add(repo[len("docker.io/"):])
    else:
        out.add("docker.io/" + repo)
    for name in list(out):
        if "/" not in name:
            out.add("library/" + name)
        if name.startswith("library/"):
            out.add(name[len("library/"):])
    return out


def split_ref(ref: str) -> tuple[str, str | None, str | None]:
    """repo, tag, digest -- a ref may carry a tag, a digest, or both.

    Returning the digest is the whole point: `repo@sha256:...` with NO tag used to
    fall out of this scan entirely, because the old two-value version split on `@`
    first and then found no `:` left to take a tag from. See the 2026-09-22 entry
    in the module docstring.
    """
    head, _, digest = ref.partition("@")
    repo = head
    if ":" in repo.rsplit("/", 1)[-1]:
        repo, tag = repo.rsplit(":", 1)
        return repo, tag, digest or None
    return repo, None, digest or None


def is_pinned(tag: str | None) -> bool:
    """A pin names ONE build. A shorter tag names a LINE that rolls on its own.

    `alpine:3.20`, `python:3.13-slim`, `busybox:1.37` and `opensuse/leap:16.0` keep
    receiving their own patch releases under the same string, so Renovate has nothing
    to bump and they are not this test's problem -- they belong to the separate
    floating-without-Keel question (they move only when a pod is recreated). Three or
    more numeric components means a specific build: 26.7.2, 1.6.0, 2026.8.22-9fea41204.
    """
    if tag is None or tag in FLOATING_TAGS or tag.startswith("stable-"):
        return False
    if MAJOR_LINE_RE.fullmatch(tag):
        return False
    m = re.match(r"v?(\d+(?:\.\d+)*)", tag)
    if not m:
        return False
    return len(m.group(1).split(".")) >= 3


def pinned_images() -> list[tuple[str, str, str, bool]]:
    """(file, repo, label, digest_only) for every PINNED image reference in tracked YAML.

    ⭐ "Pinned" now means "names one immutable build", which is EITHER a tag with three
    or more numeric components OR a ref carrying an explicit @sha256 — whatever tag it
    carries. The second half is new on 2026-10-02 and is what brings Lane A into scope:
    `python:3-alpine@sha256:…` names one build, so it can freeze in place exactly like a
    version pin, and the estate now has ~70 of them instead of a floating-tag blind
    spot. `digest_only` marks the one shape no manager setting can reach: a digest with
    NO tag.
    """
    found = []
    for rel in fleetlib.tracked_yaml():
        if os.path.basename(rel) in OUT_OF_SCOPE_FILES:
            continue
        try:
            text = (fleetlib.repo_root() / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            m = IMAGE_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            if "{{" in ref or "${" in ref:  # a template, not a resolvable pin
                continue
            repo, tag, digest = split_ref(ref)
            if is_pinned(tag):
                found.append((rel, repo, tag, False))
            elif digest:
                # Either a digest with no tag at all, or a floating/LINE tag carrying a
                # digest (Lane A). Both name exactly ONE build -- that is what a digest
                # IS. A digest with no tag is reported by its digest, because there is
                # no version string to report, and flagged so unreachable() can say why
                # it is the one shape no manager setting can move.
                found.append((rel, repo, tag or digest, tag is None))
    return sorted(set(found))


def unreachable() -> list[tuple[str, str, str, str]]:
    """(file, repo, tag, which gate failed) for pins no enabled manager can move."""
    cfg = renovate_config()
    patterns = manager_file_patterns(cfg)
    opt_ins = kubernetes_opt_ins(cfg)
    default_on = kubernetes_default_on(cfg)
    holds = held_packages(cfg)
    disabled = manager_disabled_files(cfg)
    # matchPackageNames entries an alias makes unmatchable, so the failure can say WHY.
    aliased_away = {
        n
        for rule in cfg.get("packageRules", [])
        if rule.get("enabled") is True
        and (rule.get("matchManagers") is None or "kubernetes" in rule.get("matchManagers"))
        for n in rule.get("matchPackageNames", [])
        if aliased_name(n, cfg) != n
    }

    out = []
    for rel, repo, label, digest_only in pinned_images():
        # Shape first: a digest with no tag is unreachable whatever the config says,
        # because there is no version for Renovate to compare it against. No rule can
        # express "bump this digest" when the tag that would move it is missing.
        if digest_only:
            out.append((rel, repo, label,
                        "digest with NO tag: it names one build and carries no version "
                        "for Renovate to compare, so no manager setting can move it -- "
                        "put the tag back alongside the digest"))
            continue
        seen_by = [m for m, pats in patterns.items() if any(p.search(rel) for p in pats)]
        # The fleet manager reads chart versions out of fleet.ya?ml, never image strings.
        seen_by = [m for m in seen_by if m != "fleet"]
        # ...and a manager explicitly switched off for this file is not watching it.
        seen_by = [m for m in seen_by
                   if not any(file_matches_glob(rel, g) for g in disabled.get(m, []))]
        if not seen_by:
            out.append((rel, repo, label,
                        "no manager's managerFilePatterns matches this filename"))
            continue
        if seen_by != ["kubernetes"]:
            continue
        if default_on:
            if spellings(repo) & holds:
                out.append((rel, repo, label,
                            "held by an `enabled: false` packageRule in renovate.json "
                            "(off for EVERY update type)"))
        elif not (spellings(repo) & opt_ins):
            why = ("kubernetes manager is Default OFF and this package has no "
                   "`enabled: true` rule")
            if spellings(repo) & aliased_away:
                why = ("its `enabled: true` rule matches on matchPackageNames, but "
                       "registryAliases rewrites packageName away from that name, so the "
                       "rule never matches -- switch it to matchDepNames")
            out.append((rel, repo, label, why))
    return out


class RenovateReachabilityTest(unittest.TestCase):
    def test_no_new_unreachable_pin(self):
        pins = pinned_images()

        # ⛔ Empty selection is not a pass.
        self.assertGreater(
            len(pins), 20,
            "found almost no pinned images at all -- the scan is broken, not the repo",
        )

        new = [(rel, repo, tag, why) for rel, repo, tag, why in unreachable()
               if (rel, repo) not in KNOWN_UNREACHABLE]
        msgs = [
            f"{rel}: {repo}:{tag}\n    {why}.\n"
            f"    Fix: add an `enabled: true` packageRule for {repo!r} in renovate.json "
            f"(and, if the filename is the problem, rename to *-deploy.yml / *-ds.yml or "
            f"extend managerFilePatterns). See /lane-a-to-lane-b-migration."
            for rel, repo, tag, why in new
        ]
        self.assertEqual(
            [], msgs,
            "\nPinned image(s) that NO Renovate manager can move -- frozen at this "
            "version with nothing watching for CVEs:\n" + "\n".join(msgs),
        )

    def test_baseline_has_not_gone_stale(self):
        """An entry that is now reachable (or gone) must leave the list, or the ratchet
        loosens: a future regression on that same pin would be silently forgiven."""
        still_blind = {(rel, repo) for rel, repo, _label, _why in unreachable()}
        present = {(rel, repo) for rel, repo, _label, _digest_only in pinned_images()}

        stale = []
        for key in sorted(KNOWN_UNREACHABLE):
            rel, repo = key
            if key not in present:
                stale.append(f"{rel}: {repo} -- pin is gone; drop this entry")
            elif key not in still_blind:
                stale.append(f"{rel}: {repo} -- now reachable; drop this entry")
        self.assertEqual(
            [], stale,
            "\nKNOWN_UNREACHABLE is out of date. Remove these so the ratchet stays "
            "tight:\n" + "\n".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
