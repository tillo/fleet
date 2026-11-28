"""Every bundle's Helm release must fit in ONE Secret, with headroom (infra-3rm.33).

Fleet deploys each bundle as a Helm release, and Helm keeps each revision in a
single Secret, which the API server refuses above 1,048,576 bytes of data. On
2026-11-19 mdapi-prod-monitoring-rules v712 stood at 1,047,252 B; v713 (fleet!2614,
one alert) was refused, the bundle went ErrApplied, and every later rule change
merged green and deployed nothing. f/probe/fleet_bundle_size measured the Bundle
against etcd's 1.5 MiB limit, a different object, and could not see it; the probe
also cannot read release Secrets (Windmill has no cluster-wide Secret read, on
purpose). The release is fully determined by the files in the bundle directory,
so the check belongs here, before merge.

What a release Secret holds (decoded from the live v712): base64(gzip(JSON)), where
the JSON carries every bundle file base64'd under chart.files AND the rendered
manifest, so each byte is stored about twice. estimate() rebuilds that shape and
was calibrated against live releases: monitoring-rules within 1% (1,053,816 est.
with !2614 vs 1,047,252 live without it), others over-estimate (grafana +19%),
which errs on the safe side.

Chart bundles (helm.chart set) store their chart, not their directory, so they are
excluded here and checked by tools/ci/render-bundles.py from the pulled chart and its
render (infra-3rm.35; kagent-crds stood at 855,160 B = 82% on 2026-11-20). The
estimator lives in tools/ci/release_size.py.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import fleetlib

_PATH = Path(__file__).resolve().parent.parent / "tools/ci/release_size.py"
_spec = importlib.util.spec_from_file_location("release_size", _PATH)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def bundle_files(bundle_dir: str) -> dict[str, bytes]:
    """Tracked files a bundle ships, minus those owned by a nested bundle."""
    prefix = bundle_dir + "/" if bundle_dir else ""
    nested = [d + "/" for d, _ in fleetlib.bundles() if d != bundle_dir and d.startswith(prefix)]
    out = {}
    for f in fleetlib.tracked_files():
        if not f.startswith(prefix) or any(f.startswith(n) for n in nested):
            continue
        rel = f[len(prefix):]
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        out[rel] = (fleetlib.repo_root() / f).read_bytes()
    return out


class EveryBundleFits(unittest.TestCase):
    def test_every_bundle_release_fits_with_headroom(self):
        ## Chart bundles store their CHART, not the directory: tools/ci/render-bundles.py
        ## checks those from the pulled chart and its render (infra-3rm.35).
        estimates = {d: rs.estimate(bundle_files(d)) for d, m in fleetlib.bundles()
                     if d and not (fleetlib.bundle_spec(m).get("helm") or {}).get("chart")}
        self.assertGreater(len(estimates), 100, "bundle discovery found almost nothing")
        self.assertEqual(rs.oversized(estimates), [])


if __name__ == "__main__":
    unittest.main()
