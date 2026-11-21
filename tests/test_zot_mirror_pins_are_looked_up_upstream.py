"""A pin on a zot PULL-THROUGH path must be looked up upstream, or it is reachable but never updatable.

infra-hcpa (found 2026-10-01 on litellm, generalised 2026-11-11): zot's `/v2/<repo>/tags/list`
answers from zot's OWN storage. A sync prefix makes a KNOWN tag fetchable; it never
enumerates upstream, so it never reveals a NEW tag (a synced repo whose pin was 0.161.0
listed exactly ['0.161.0']). Renovate's docker datasource reads tags/list, so a pin
written as `zot.mdapi.ch/<synced path>:<tag>` gets no update, ever, while Renovate stays
green and test_every_pinned_image_is_renovate_reachable.py stays green: that test asks
whether a manager READS the pin, and it does. Reachable is not updatable.

The fix (litellm, fleet !639) is `registryAliases`: the LOOKUP goes to the upstream
registry while the manifest keeps the zot reference, so the pull-through stays the only
supply path. This file asserts that for EVERY pin on a synced path, derived from zot's
own sync config rather than restated:

  * kubernetes `image:` pins       -> kubernetes.registryAliases
  * helm-values `repository:` pins -> helm-values.registryAliases
  * custom.regex managers           -> packageNameTemplate (aliases do not apply there)

and that no packageRule names an aliased-away spelling in `matchPackageNames`
(registryAliases rewrites packageName, so such a rule silently stops matching; name the
mirror spelling with `matchDepNames`, or the upstream spelling with matchPackageNames).

Hand-pushed repos (`zot.mdapi.ch/mdapi/...`, built by our own CI) are out of scope: zot
IS their origin, so its tags/list is the complete answer.
"""

from __future__ import annotations

import json
import re
import unittest
from urllib.parse import urlparse

import yaml

import fleetlib
from test_every_pinned_image_is_renovate_reachable import pinned_images, renovate_config

ZOT = "zot.mdapi.ch"
ZOT_CONFIG = "zot/zot-s3-config-es.yml"
REPOSITORY_RE = re.compile(r"^\s*repository:\s*[\"']?(zot\.mdapi\.ch/[^\s\"'#]+)")
TEMPLATE_RE = re.compile(r"\{\{[^}]*\}\}")
REGISTRY_RE = re.compile(r"^\s*registry:\s*[\"']?zot\.mdapi\.ch[\"']?\s*$")
PLAIN_REPOSITORY_RE = re.compile(r"^\s*repository:\s*[\"']?([^\s\"'#/][^\s\"'#]*)")


def sync_prefixes() -> list[tuple[str, str]]:
    """(content prefix, upstream host) for every prefix zot pulls through."""
    docs = [d for d in yaml.safe_load_all((fleetlib.repo_root() / ZOT_CONFIG).read_text())
            if isinstance(d, dict)]
    raw = docs[0]["spec"]["target"]["template"]["data"]["config.json"]
    cfg = json.loads(TEMPLATE_RE.sub("x", raw))
    out = []
    for reg in cfg["extensions"]["sync"]["registries"]:
        hosts = [urlparse(u).netloc for u in reg["urls"]]
        out.extend((c["prefix"], hosts[0]) for c in reg["content"])
    return out


def upstream_of(repo: str, prefixes: list[tuple[str, str]]) -> str | None:
    """`host/path` zot fetches this mirror repo from, or None for a hand-pushed repo."""
    if not repo.startswith(ZOT + "/"):
        return None
    path = repo[len(ZOT) + 1:]
    for prefix, host in prefixes:
        if prefix.endswith("/**"):
            if path.startswith(prefix[:-2]):
                return f"{host}/{path}"
        elif path == prefix:
            return f"{host}/{path}"
    return None


def resolve(name: str, aliases: dict[str, str]) -> str:
    """The packageName Renovate looks up after registryAliases (prefix rewrite)."""
    for prefix, replacement in aliases.items():
        if name == prefix or name.startswith(prefix.rstrip("/") + "/"):
            return replacement + name[len(prefix):]
    return name


def helm_values_pins() -> list[tuple[str, str]]:
    """(file, repo) for every zot pin in a values file, in both shapes helm-values reads:
    `repository: zot.mdapi.ch/<path>`, and `registry: zot.mdapi.ch` + `repository: <path>`
    (Renovate joins the two, so the lookup name is the same)."""
    found = []
    for rel in fleetlib.tracked_yaml():
        if not re.search(r"(^|/)values\.ya?ml$", rel):
            continue
        previous = ""
        for line in (fleetlib.repo_root() / rel).read_text(encoding="utf-8").splitlines():
            m = REPOSITORY_RE.match(line)
            r = PLAIN_REPOSITORY_RE.match(line)
            if m:
                found.append((rel, m.group(1)))
            elif r and REGISTRY_RE.match(previous):
                found.append((rel, f"{ZOT}/{r.group(1)}"))
            previous = line
    return found


def blind(pins, aliases, prefixes) -> list[str]:
    out = []
    for rel, repo in pins:
        want = upstream_of(repo, prefixes)
        got = resolve(repo, aliases)
        if want is not None and got != want:
            out.append(f"{rel}: {repo} is looked up at {got}, which lists only cached "
                       f"tags; alias it to {want}")
    return out


class ZotMirrorPinsAreLookedUpUpstreamTest(unittest.TestCase):
    def setUp(self):
        self.cfg = renovate_config()
        self.prefixes = sync_prefixes()

    def test_the_sync_config_was_read(self):
        self.assertGreater(len(self.prefixes), 50,
                           "measured NOTHING: zot sync prefixes did not parse")

    def test_kubernetes_pins_on_a_synced_path_are_aliased_upstream(self):
        pins = [(rel, repo) for rel, repo, _l, _d in pinned_images()
                if upstream_of(repo, self.prefixes)]
        self.assertGreaterEqual(len(pins), 5, "measured NOTHING: no zot mirror pins found")
        aliases = (self.cfg.get("kubernetes") or {}).get("registryAliases") or {}
        self.assertEqual([], blind(pins, aliases, self.prefixes))

    def test_helm_values_pins_on_a_synced_path_are_aliased_upstream(self):
        pins = [(rel, repo) for rel, repo in helm_values_pins()
                if upstream_of(repo, self.prefixes)]
        self.assertGreaterEqual(len(pins), 3, "measured NOTHING: no values-file mirror pins")
        aliases = (self.cfg.get("helm-values") or {}).get("registryAliases") or {}
        self.assertEqual([], blind(pins, aliases, self.prefixes))

    def test_custom_regex_managers_on_a_synced_path_look_up_upstream(self):
        problems = []
        for cm in self.cfg.get("customManagers", []):
            dep = cm.get("depNameTemplate") or ""
            want = upstream_of(dep, self.prefixes)
            if want is not None and cm.get("packageNameTemplate") != want:
                problems.append(f"{dep}: set packageNameTemplate to {want!r}")
        self.assertEqual([], problems)

    def test_no_rule_names_an_aliased_away_package_name(self):
        aliases = {}
        for mgr in ("kubernetes", "helm-values"):
            aliases.update((self.cfg.get(mgr) or {}).get("registryAliases") or {})
        dead = sorted({n for rule in self.cfg.get("packageRules", [])
                       for n in rule.get("matchPackageNames", [])
                       if resolve(n, aliases) != n})
        self.assertEqual(
            [], dead,
            "matchPackageNames entries in the MIRROR spelling never match: registryAliases "
            "rewrites packageName. Use matchDepNames for the mirror spelling.")

    def test_resolver_rewrites_a_prefix_only_on_a_label_boundary(self):
        aliases = {"zot.mdapi.ch/library": "docker.io/library"}
        self.assertEqual(resolve("zot.mdapi.ch/library/python", aliases),
                         "docker.io/library/python")
        self.assertEqual(resolve("zot.mdapi.ch/libraryx/python", aliases),
                         "zot.mdapi.ch/libraryx/python")

    def test_upstream_of_honours_exact_and_globbed_prefixes(self):
        prefixes = [("library/python", "docker.io"), ("rancher/**", "docker.io")]
        self.assertEqual(upstream_of("zot.mdapi.ch/library/python", prefixes),
                         "docker.io/library/python")
        self.assertIsNone(upstream_of("zot.mdapi.ch/library/pythonx", prefixes))
        self.assertEqual(upstream_of("zot.mdapi.ch/rancher/a/b", prefixes),
                         "docker.io/rancher/a/b")
        self.assertIsNone(upstream_of("zot.mdapi.ch/mdapi/bussola", prefixes))
        self.assertIsNone(upstream_of("docker.io/library/python", prefixes))


if __name__ == "__main__":
    unittest.main()
