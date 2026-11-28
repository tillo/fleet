"""Each redis-ha set must run its own sentinel group name.

WHY THIS FILE EXISTS
Four redis-ha session stores (oauth2-proxy tv, admin, portal, bussola) share
namespace oauth2-proxy, have no auth and no NetworkPolicy, and all used the
chart's default group name `mymaster`. A sentinel takes in any sentinel or
replica that announces itself for a master of the same NAME. On 2026-11-23/24
the tv and admin sentinels merged into one mesh of six (each saw 5 others), and
the admin sentinels took tv's redis-0 as one of their replicas. They kept
pointing it at the admin master, tv's split-brain-fix sidecar kept shutting it
down, and it crash-looped 40 times. mdapi-prod-oauth2-proxy-tv sat NotReady and
FleetBundlesNotReadySustained fired (o2 row infra-dg51s, bead infra-r9mzd.84).
A `sentinel reset` on only one side re-merged the mesh within 15 s.

The group name is the release name. The probe f/probe/redis_ha_health asks each
store's sentinels for the master by that name, so the oauth2-proxy client, the
postStart `sentinel reset` hook and the probe must all agree on it.
"""

import unittest

import fleetlib


def _redis_ha_sets():
    """[(values path, release name, values)] for each bundle that enables redis-ha."""
    out = []
    for bundle_dir, marker in fleetlib.bundles():
        spec = fleetlib.bundle_spec(marker)
        helm = spec.get("helm") or {}
        for vf in helm.get("valuesFiles") or ():
            rel = f"{bundle_dir}/{vf}"
            values = fleetlib.load_docs(rel)[0] or {}
            if (values.get("redis-ha") or {}).get("enabled"):
                out.append((rel, helm["releaseName"], values))
    return out


class RedisHaSentinelGroupsAreUniqueTest(unittest.TestCase):
    def setUp(self):
        self.sets = _redis_ha_sets()

    def test_some_sets_are_found(self):
        # Guard against a discovery bug passing every test below vacuously.
        self.assertGreaterEqual(len(self.sets), 4)

    def test_group_name_is_the_release_name(self):
        for rel, release, values in self.sets:
            with self.subTest(values=rel):
                group = ((values["redis-ha"].get("redis") or {})).get("masterGroupName")
                self.assertEqual(
                    group, release,
                    "redis-ha.redis.masterGroupName must be the release name: the chart "
                    "default `mymaster` lets sets in one namespace adopt each other",
                )

    def test_oauth2_proxy_asks_for_its_own_group(self):
        for rel, release, values in self.sets:
            with self.subTest(values=rel):
                sentinel = values["sessionStorage"]["redis"]["sentinel"]
                self.assertEqual(sentinel["masterName"], release)

    def test_poststart_reset_names_its_own_group(self):
        for rel, release, values in self.sets:
            with self.subTest(values=rel):
                cmd = " ".join(
                    values["redis-ha"]["sentinel"]["lifecycle"]["postStart"]["exec"]["command"]
                )
                self.assertIn(f"sentinel reset {release}", cmd)
                self.assertNotIn("mymaster", cmd)


if __name__ == "__main__":
    unittest.main()
