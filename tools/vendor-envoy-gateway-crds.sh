#!/usr/bin/env bash
# Render the Envoy Gateway CRDs (gateway.envoyproxy.io only) for one chart version
# into envoy-gateway-crds/crds.yaml and envoy-gateway-crds-envoyproxy/crds.yaml.
# Run it whenever envoy-gateway/fleet.yaml moves;
# tests/test_envoy_gateway_crds_match_controller.py fails until you do.
#
#   tools/vendor-envoy-gateway-crds.sh v1.8.6
#
# Why rendered and not a Helm bundle: see that test's docstring (release Secret
# 1.87 MB > 1 MiB). Gateway API CRDs stay OFF — RKE2 owns them.
set -euo pipefail

version=${1:?usage: $0 <gateway-crds-helm version, e.g. v1.8.6>}
root=$(git rev-parse --show-toplevel)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

helm pull oci://docker.io/envoyproxy/gateway-crds-helm --version "$version" -d "$work" >/dev/null
helm template envoy-gateway-crds "$work"/gateway-crds-helm-"$version".tgz \
  --set crds.gatewayAPI.enabled=false --set crds.envoyGateway.enabled=true > "$work/all.yaml"

# Two bundles, each under the 1 MiB release-Secret limit: the EnvoyProxy CRD alone
# is two thirds of the set (see the test's docstring).
python3 - "$work/all.yaml" "$version" "$root/envoy-gateway-crds/crds.yaml" \
  "$root/envoy-gateway-crds-envoyproxy/crds.yaml" <<'PY'
import re, sys
src, version, rest_out, proxy_out = sys.argv[1:]
docs = [d for d in re.split(r"^---$", open(src).read(), flags=re.M) if d.strip()]
groups = {m for d in docs for m in re.findall(r"^  group: (\S+)$", d, re.M)}
if groups != {"gateway.envoyproxy.io"}:
    sys.exit(f"unexpected CRD groups: {sorted(groups)}")  # the chart can render Gateway API
head = (f"# rendered from oci://docker.io/envoyproxy/gateway-crds-helm {version}\n"
        "# by tools/vendor-envoy-gateway-crds.sh — do not edit by hand.\n")
is_proxy = lambda d: re.search(r"^  name: envoyproxies\.gateway\.envoyproxy\.io$", d, re.M)
for out, part in ((proxy_out, [d for d in docs if is_proxy(d)]),
                  (rest_out, [d for d in docs if not is_proxy(d)])):
    open(out, "w").write(head + "".join("---" + d.rstrip("\n") + "\n" for d in part))
    print(f"{out}: {len(part)} CRDs for {version}")
PY
