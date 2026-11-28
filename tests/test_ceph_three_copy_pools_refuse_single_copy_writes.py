"""A pool that keeps 3 copies must not accept writes onto a single one.

`min_size 1` was the 2026-06-13 decision for 2-copy pools, where it is the only way
to keep writing through a node reboot. On 2026-08-02 the bulk pools went to 3 copies
(one per host) and the `min_size: "1"` lines were carried over. With 3 copies a whole
node can be down and 2 copies still remain, so the 1 no longer buys reboot survival;
it only matters when TWO copies are down at once, the normal state of affairs on the
TRIM-less consumer SSDs (osd.2/7/17 peer-marked down together at 2026-11-04 17:22Z,
osd.3 killed at 22:43Z). Then Ceph keeps writing to a single drive, and losing or
diverging that drive loses the newest writes. User decision 2026-11-05
(infra-r9mzd.20): min_size 2 on every 3-copy pool.

Rook re-applies `parameters` on every reconcile, but a pool with NO min_size
parameter keeps whatever it has live (1, from `osd_pool_default_min_size = 1`), so
every pool declared here must state its min_size explicitly (size - size/2). The
ceph.conf `osd_pool_default_min_size = 1` is left alone on purpose: it only seeds
pools Rook does not declare (.mgr, default.rgw.*, all 2-copy), and touching the
override is a daemon-restart event on drives that stall on restart.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator

import yaml

from tests.fleetlib import repo_root


def values() -> dict:
    return yaml.safe_load((repo_root() / "rook-ceph-cluster" / "values.yml").read_text())


def pool_specs(v: dict) -> Iterator[tuple[str, dict]]:
    """Every pool spec Rook creates from these values, with a readable name."""
    for p in v.get("cephBlockPools", []):
        yield p["name"], p["spec"]
    for fs in v.get("cephFileSystems", []):
        yield f'{fs["name"]}.metadata', fs["spec"]["metadataPool"]
        for d in fs["spec"].get("dataPools", []):
            yield f'{fs["name"]}.{d["name"]}', d
    for os_ in v.get("cephObjectStores", []):
        yield f'{os_["name"]}.metadata', os_["spec"]["metadataPool"]
        yield f'{os_["name"]}.data', os_["spec"]["dataPool"]


def required_min_size(size: int) -> int:
    return size - size // 2


class ThreeCopyPoolsRefuseSingleCopyWrites(unittest.TestCase):
    def test_every_pool_declares_the_size_derived_min_size(self):
        for name, spec in pool_specs(values()):
            size = spec["replicated"]["size"]
            with self.subTest(pool=name, size=size):
                got = spec.get("parameters", {}).get("min_size")
                self.assertEqual(got, str(required_min_size(size)))

    def test_the_walk_finds_every_pool_kind(self):
        names = [n for n, _ in pool_specs(values())]
        self.assertEqual(len(names), 5, names)

    def test_required_min_size(self):
        self.assertEqual([required_min_size(s) for s in (1, 2, 3, 4)], [1, 1, 2, 2])


if __name__ == "__main__":
    unittest.main()
