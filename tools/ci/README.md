# Fleet bundle validation (CI)

Three gates that run on every push and MR, in the `lint` stage, as job `fleet-validate`.
They exist because until 2026-09-12 this repo's CI could not see inside a Helm chart:
`lint.py` says so in its own comments -- it "can only see RAW
Deployment/StatefulSet/DaemonSet manifests in this repo -- NOT workloads whose
imagePullPolicy lives in Helm values". 31 of our 154 bundles are chart-based, and
nothing had ever checked what they render to.

None of this is Argo CD tooling. Rendering a GitOps repo in CI is a CI technique, not a
CD feature -- plain `helm template` and `kubectl kustomize`.

## 1. `bundle-gate.py` -- can Fleet even build this?

`fleet apply -o -` per bundle, offline, no cluster and no kubeconfig. Catches two Fleet
failures a schema check cannot see:

* a chart ref that does not resolve -- Fleet turns that into a **Stalled GitRepo**, and a
  Stall freezes bundling for the **whole repo** until `forceSyncGeneration`. One bad
  version bump is a repo-wide outage, so this is the highest-value gate of the three.
* a bundle too large for etcd. The Bundle this emits is what goes to etcd, so its byte
  size is the real measurement, not an estimate. Warns at 800 KiB, fails at 1400 KiB.
  Largest today: `volsync` at 854 KB (already warning), `monitoring-rules` at 716 KB.

⛔ `fleet apply` needs a path **relative to the repo root**. Given an absolute path it
exits 1 with `no resource found at the following paths to deploy`, which reads like a
broken bundle rather than a bad invocation.

## 2. `render-bundles.py` -- what do the charts actually produce?

Discovers bundles from `git ls-files`, accepting **both `fleet.yaml` and `fleet.yml`**
(Fleet reads either; 32 of our bundles use `.yml`, and an `*.yaml`-only sweep silently
skips them -- that mistake is why the first survey of this repo undercounted bundles by
a third).

For each chart bundle it runs `helm template` with the bundle's pinned chart, repo,
version, namespace, `valuesFiles` and inline `values`, plus `--include-crds` and
`--kube-version`. Four bundles (`cribl`, `envuassu/nextcloud`, `metallb`,
`nextcloud/nextcloud`) then get Fleet's helm->kustomize post-render reproduced: the
rendered output is fed in as the kustomize base and the bundle's kustomization applied
on top. Verified against metallb -- the frr container's
`livenessProbe.timeoutSeconds` is absent from the chart output and becomes 5 after the
post-render, exactly what that patch claims to do.

Unsupported options **fail loudly** rather than render something that is not what Fleet
would deploy: `helm.valuesFrom` is rejected, and any unrecognised `helm:` key errors so
a new Fleet feature cannot be silently ignored.

Helm is run hermetically (`HELM_REPOSITORY_CONFIG` etc. pointed at a scratch dir).
Without that, `helm template --repo ...` still consults the caller's repo list and can
die on an unrelated stale entry.

⛔ **Never publish the rendered directory as a CI artifact.** Three charts (horizon,
victoria-metrics-operator, vpa) generate a fresh self-signed CA and private key on every
render -- verified by rendering twice and diffing. The job writes it to `$CI_PROJECT_DIR`
scratch and does not upload it.

## 3. `validate-manifests.py` -- is any of it valid?

`kubeconform -strict` against the cluster's Kubernetes version over both the rendered
output and every tracked YAML that is really a manifest. "Really a manifest" is decided
by content -- every document must carry apiVersion+kind -- so values files and CI config
drop out without a hand-maintained exclude list that could rot. Bundle configs and
kustomize inputs are excluded by path (patch fragments are partial by design).

CRD schemas come from the datreeio CRDs-catalog. A kind with no schema is a **coverage
gap that fails the job**, not a pass, unless it is listed with a reason in
`validation-exceptions.yaml`.

⚠️ Exceptions match on **apiVersion+kind, never kind alone**. `ClusterIssuer` exists in
both `cert-manager.io/v1` (schema present) and `horizon.evertrust.io/v1beta1` (none);
`NetworkPolicy` in both `networking.k8s.io` and `crd.projectcalico.org`. A kind-only
exception would switch off validation for the covered one too.

## Running it locally

```sh
tools/ci/bundle-gate.py
tools/ci/render-bundles.py --output /tmp/fleet-rendered      # must be empty
tools/ci/validate-manifests.py --rendered /tmp/fleet-rendered
```

⚠️ Both discovery paths use `git ls-files`, so a **new manifest that is not yet `git
add`ed is invisible** to the validator. That is right for CI, which validates a commit,
but locally it means an untracked file passes by not being looked at.

## Pinned versions

`kubernetesVersion` in the CI job must track the cluster (currently `v1.35.2+rke2r1`).
Binaries are pinned by version **and sha256** in `.gitlab-ci.yml`; update both together.
Helm 4.2.3 and 4.3.0 were diffed across all 31 bundles: the only differences are blank
lines, so the exact patch version is cosmetic here.

## Known gaps worth closing

* CRDs themselves are unvalidated -- kubeconform skips `CustomResourceDefinition` by
  design (112 of them in the rendered output). Explicit group/name/scope/storage-version
  checks would close that.
* Four CRD kinds have no published schema (see `validation-exceptions.yaml`). The Calico
  `NetworkPolicy` one matters most: it is the kind the isolation programme writes.
* **The external catalog can be STALE, which produces false failures.** Observed
  2026-09-12: the catalog's `cephcluster_v1.json` rejects
  `spec.security.cephx.csi.keyType`, a chart default that the CRD actually installed on
  mdapi-prod declares perfectly well. That is now a documented entry in
  `validation-exceptions.yaml`, but the entry is a workaround, not an answer.
* Generating schemas from the CRDs the charts themselves render would remove the
  dependency on an external catalog and close all three gaps at once.
