"""envoy-gateway-crds/ holds the Envoy Gateway CRDs as RENDERED manifests, and they
must be the ones for the controller version envoy-gateway/fleet.yaml installs.

Why vendored (2026-11-25, MR !2890): as a Helm bundle of gateway-crds-helm the release
Secret measured 1.87 MB (render-bundles release-size gate, 1 MiB limit). Helm stores
EVERY chart template in the release, and that chart ships the full Gateway API CRDs
as templates even when they render nothing — which they must not here: Gateway API
is owned by RKE2. The eight gateway.envoyproxy.io CRDs alone fit.

The cost of vendoring is drift: Renovate bumps the controller chart and nothing
bumps rendered YAML. These tests turn that drift into a red pipeline, so a bump
MR cannot merge until tools/vendor-envoy-gateway-crds.sh has been re-run.
"""
import re
import unittest

import yaml

from tests import fleetlib

## Two bundles: one CRD (EnvoyProxy, 1.59 MB raw) is two thirds of the set, and a
## raw bundle stores each file twice (template + manifest), so one bundle of all
## eight still estimated 104.6% of 1 MiB. Split: 53.8% and 51.1%.
CRD_FILES = ("envoy-gateway-crds/crds.yaml", "envoy-gateway-crds-envoyproxy/crds.yaml")
CONTROLLER = "envoy-gateway/fleet.yaml"


def text(rel: str) -> str:
    return (fleetlib.repo_root() / rel).read_text()


def vendored_version(rel: str) -> str:
    m = re.search(r"^# rendered from oci://docker\.io/envoyproxy/gateway-crds-helm (\S+)$",
                  text(rel), re.M)
    assert m, f"{rel} must start with its '# rendered from ... <version>' header"
    return m.group(1)


def crds(rel: str) -> list:
    return [d for d in yaml.safe_load_all(text(rel)) if d]


class VendoredEnvoyGatewayCrds(unittest.TestCase):
    def test_vendored_crds_are_the_controller_version(self):
        controller = yaml.safe_load(text(CONTROLLER))["helm"]["version"]
        for rel in CRD_FILES:
            self.assertEqual(
                vendored_version(rel), controller,
                f"{rel} was rendered for {vendored_version(rel)} but {CONTROLLER} installs "
                f"{controller}: run tools/vendor-envoy-gateway-crds.sh {controller}")

    def test_only_envoy_gateway_crds_never_gateway_api(self):
        groups = {d["spec"]["group"] for rel in CRD_FILES for d in crds(rel)}
        self.assertEqual(groups, {"gateway.envoyproxy.io"},
                         "Gateway API CRDs are owned by RKE2 (rke2-traefik-crd); never vendor them")

    def test_all_eight_kinds_are_present(self):
        kinds = sorted(d["spec"]["names"]["kind"] for rel in CRD_FILES for d in crds(rel))
        self.assertEqual(kinds, sorted([
            "Backend", "BackendTrafficPolicy", "ClientTrafficPolicy", "EnvoyExtensionPolicy",
            "EnvoyPatchPolicy", "EnvoyProxy", "HTTPRouteFilter", "SecurityPolicy"]))

    def test_the_crd_bundles_are_raw_not_a_chart(self):
        for rel in CRD_FILES:
            fleet = yaml.safe_load(text(rel.replace("crds.yaml", "fleet.yaml")))
            self.assertNotIn("chart", fleet.get("helm", {}),
                             "a gateway-crds-helm release Secret exceeds 1 MiB — keep the bundles raw")

    def test_the_controller_waits_for_both_crd_bundles(self):
        deps = {d["name"] for d in yaml.safe_load(text(CONTROLLER))["dependsOn"]}
        self.assertEqual(deps, {"mdapi-prod-envoy-gateway-crds", "mdapi-prod-envoy-gateway-crds-envoyproxy"})


if __name__ == "__main__":
    unittest.main()
