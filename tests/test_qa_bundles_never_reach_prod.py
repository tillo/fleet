"""Off-prod bundles and prod bundles are rendered by different GitRepos, and never cross.

infra-7rog.41.6 (2026-11-23): the IAST/DAST QA cluster runs on a third-party cloud
(Rackspace Spot) and is driven by its OWN standalone Fleet, whose GitRepo renders exactly
the paths in qa-paths.txt. gitrepo/mdapi-prod renders exactly fleet-paths.txt.
2026-11-24: the `spot` GitLab CI runner lives in a SECOND Spot cluster (mdapi-ci), kept
apart from QA because QA may hold only synthetic data while CI pods hold real checkouts.
It is rendered the same way, from spot-ci-paths.txt.

Two ways this goes wrong, both silent:

* an off-prod directory listed in fleet-paths.txt is appended to gitrepo/mdapi-prod and
  ships its manifests to PROD;
* a prod directory (or another cluster's directory) listed in an off-prod list ships that
  bundle, with whatever it references, to a third-party cluster.

So each off-prod list may list only its own prefix, fleet-paths.txt none of them, and
every bundle under a prefix must be listed in that prefix's file (lint.py check F would
otherwise report it as missing from fleet-paths.txt, and the obvious "fix" is the first
failure above).
"""

from __future__ import annotations

import unittest

from fleetlib import bundles, repo_root

# prefix -> the path list its cluster's standalone Fleet renders. ONE table: lint.py
# check F reads the same mapping (OFF_PROD there); keep them equal.
OFF_PROD = {
    "qa": "qa-paths.txt",
    "spot-ci": "spot-ci-paths.txt",
}


def listed(name: str) -> set[str]:
    text = (repo_root() / name).read_text()
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")}


def under(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def off_prod(path: str) -> bool:
    return any(under(p, path) for p in OFF_PROD)


class OffProdBundlesNeverReachProd(unittest.TestCase):
    def test_each_off_prod_list_holds_only_its_own_prefix(self):
        for prefix, name in OFF_PROD.items():
            with self.subTest(list=name):
                stray = sorted(p for p in listed(name) if not under(prefix, p))
                self.assertEqual(stray, [], f"{name} lists paths outside {prefix}/; they would ship to that cluster")

    def test_prod_paths_list_no_off_prod_directory(self):
        stray = sorted(p for p in listed("fleet-paths.txt") if off_prod(p))
        self.assertEqual(stray, [], "fleet-paths.txt lists off-prod paths; they would ship to PROD")

    def test_every_off_prod_bundle_is_listed_for_its_gitrepo(self):
        dirs = {d for d, _ in bundles()}
        for prefix, name in OFF_PROD.items():
            with self.subTest(list=name):
                mine = {d for d in dirs if under(prefix, d)}
                self.assertTrue(mine, f"no {prefix}/ bundle found: that GitRepo would render nothing")
                self.assertEqual(sorted(mine - listed(name)), [])

    def test_lint_check_f_uses_the_same_table(self):
        src = (repo_root() / "lint.py").read_text()
        for prefix, name in OFF_PROD.items():
            with self.subTest(prefix=prefix):
                self.assertIn(f'"{prefix}": REPO / "{name}"', src,
                              "lint.py OFF_PROD must map the same prefix to the same list")


if __name__ == "__main__":
    unittest.main()
