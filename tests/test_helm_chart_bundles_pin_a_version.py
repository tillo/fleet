"""Every Helm chart bundle must pin an explicit chart version.

Without `helm.version`, Fleet resolves whatever the chart repo calls latest at bundle
time, so the deployed version changes on its own with no commit and no MR -- the opposite
of what this repo is for. keel/fleet.yml records exactly that history: "Pinned + bumped
by Renovate ... Was: unpinned."

An unpinned chart also defeats the bundle-gate: a chart ref that stops resolving is
supposed to fail in CI, but `latest` almost always resolves to something.
"""

import unittest

import fleetlib


class HelmChartVersionPinTest(unittest.TestCase):
    def test_every_chart_bundle_pins_a_version(self):
        chart_bundles = []
        unpinned = []

        for bundle_dir, marker in fleetlib.bundles():
            helm = (fleetlib.bundle_spec(marker).get("helm") or {})
            chart = helm.get("chart")
            if not chart:
                continue
            chart_bundles.append(bundle_dir)
            version = helm.get("version")
            if not version or not str(version).strip():
                unpinned.append(
                    f"{marker}: chart {chart!r} has no helm.version -- Fleet will "
                    f"resolve 'latest' at bundle time and upgrade without a commit"
                )

        # ⛔ Empty selection is not a pass -- see the note in the sibling test.
        self.assertGreater(
            len(chart_bundles), 0,
            "no chart-based bundles found at all -- discovery is broken",
        )
        self.assertEqual([], unpinned, "\n" + "\n".join(unpinned))


if __name__ == "__main__":
    unittest.main()
