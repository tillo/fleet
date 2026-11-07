# Repo invariant tests

`python3 -m unittest discover -s tests` — runs in the `fleet-validate` CI job, and takes
about 15 s.

## The rule these exist to serve

**When a finding shows up twice, it becomes a test here — not another paragraph in a
comment.** This estate is very good at writing down what bit it; the gap is that a written
rule does nothing at 03:00 during an unattended Renovate merge. A test does.

Each file below encodes one incident that actually happened. The comment at the top of
each is the incident, not a description of the code — read it before changing the
assertion.

| test | the incident it encodes |
|---|---|
| `test_namespaced_bundle_no_cluster_scoped.py` | A cluster-scoped GlobalNetworkPolicy was committed to the `tv` bundle, whose `fleet.yaml` pins `namespace: tv`. Fleet applied the commit, reported the bundle **1/1 READY**, and the policy never appeared. Nothing errored. |
| `test_helm_chart_bundles_pin_a_version.py` | An unpinned `helm.chart` resolves to whatever the repo calls latest at bundle time, so the deployed version moves with no commit. A since-deleted bundle's `fleet.yml` recorded exactly this ("Was: unpinned"). |
| `test_kubevirt_comparepatch_keeps_runstrategy.py` | Excluding `/spec/runStrategy` from a VM's Fleet diff hides the VM being in the wrong power state, and the bundle sits NotReady forever with no clue why. |
| `test_alert_rules_carry_a_human_summary.py` | A page whose text does not say what happened costs a diagnosis round-trip. Currently true of all 488 rules — which is exactly when it is cheap to lock in. |
| `test_every_pinned_image_is_renovate_reachable.py` | Four separate pins froze silently because NO manager was watching them (`alpine/k8s` for a year, 12 values.yml files, `litellm`, OpenObserve for six weeks). A package excluded by `enabled: false` is never even attempted, so it emits no MR, no dashboard row and no log line. |
| `test_every_floating_image_has_an_updater.py` | The other half of that: a floating tag with no digest moves only when a node happens to recreate the pod — no commit, no revision, no MR. No annotation can exempt a workload: the digest is the only updater. |
| `test_harvester_rewritten_values_are_canonical.py` | After a certificate renewal the rke2-ingress-nginx bundle sat **Modified** with nothing wrong: Harvester's ssl-certificates syncer re-marshals the HelmChartConfig's `valuesContent` with yaml.v3 on every renewal, and git held a commented, hand-ordered spelling of the same values. Git now holds Harvester's exact bytes; `yamlv3.py` models the rewrite byte for byte, and fails closed. |
| `test_cis_baseline_matches_kube_bench_pin.py` | A Renovate bump of kube-bench (v0.13.0 → v0.16.0) fired CisBenchmarkRegression on every node with nothing on the estate changed: the new release measures a different set of checks. The rule comment already said "keep the baseline pinned to the tool version". The version named in CisBenchmarkToolChanged must now equal the CronJob's image tag, so the bump MR stays red until the baseline is re-derived. |
| `test_graduation_ladder.py` | Three silent policy failures in one config: `automergeSchedule` gated nothing under `platformAutomerge` (merge-when-pipeline-succeeds is armed at MR creation — bot merges landed at 16:11Z inside a written 00–06 window); the ladder had no digest rung, so 19 of 19 graduated packages still held their digest MRs, the one class that carries base-image security fixes; and `caddy` sat on the GRADUATED list while the manifest writes `library/caddy`, graduating nothing for months. |
| `test_modsec_host_selectors_are_port_tolerant.py` | `REQUEST_HEADERS:Host` keeps the `:443` that nginx strips for routing, so a `$`-anchored host list missed it — and because 1914 is a NEGATION, the port-suffixed Host fell into its DetectionOnly catch-all. Every ENFORCED host was bypassable by one header: same payload, `Host: webmail.mdapi.ch` → 403, `Host: webmail.mdapi.ch:443` → 404 (infra-ajq0). Only the weakening direction, so no FP audit surfaced it in three passes. |
| `test_modsec_detection_only_hosts_are_sanctioned.py` | Rule 1913 was a SOAK list ("promote by deleting, one host at a time, after checking its audit log is clean") and nothing was ever deleted: after seven weeks it held 26 entries, 21 of them with a clean audit log, one host with no Ingress and one literal `*.pages.mdapi.ch` no Host header can equal. DetectionOnly lets attacks through, so every remaining entry now needs a written reason in the test (infra-lcrt). |
| `test_kagent_tools_are_granted_at_the_gateway.py` | The gateway's Backstage tool allowlist named the tools bare while Backstage serves them as `catalog.<action>`: tools/list came back `[]` and every call "unknown resource", with nothing erroring (fleet !1711). A kagent Agent tool that the gateway filters out, or a RemoteMCPServer whose route the `kagent` key holds no grant for (403 inside the agent pod, while the CR still says Accepted), fails the same quiet way. Every Agent tool behind agentgateway must now pass the route grant and the backend tool authorization. |
| `test_namespace_labels_agree_across_bundles.py` | Since Fleet v0.16 every bundle deploying into a namespace resets its labels to its OWN `namespaceLabels`. On the day the PSA labels were declared (2026-11-04) a gitlab deploy stripped bootstrap's, then a jump-rbac deploy stripped jump's `enforce: privileged` — the label the seat's own pod depends on. Nothing errored. |
| `test_cnpg_clusters_declare_backup_class.py` | CNPG creates its instance PVCs itself and re-creates one on every instance rebuild, so a live `mdapi.ch/backup-class` label (the 09-29 openfga fix) dies at the next rebuild: grafana-pg and guac-pg were rebuilt 10-26/27 and LonghornBackupClassUndeclared paged for a week (infra-r9mzd). Every Cluster now declares the class in `spec.inheritedMetadata.labels`; the pre-epoch backlog is an explicit LEGACY list that may only shrink. |
| `test_changed_image_digests_have_linux_amd64.py` | fleet 80788d98 (Renovate caddy 2.11.8 tag bump, automerged 2026-11-04) pinned a digest Docker Hub serves as an OCI index with ZERO manifests; k8s-mcp had 0 ready replicas for 3.5 h while every gate was green, because nothing asked the registry. `tools/ci/image-platforms.py` (fleet-validate step 0c) now resolves every ADDED `image: ...@sha256:` and fails without linux/amd64; this test pins its decisions offline (infra-r9mzd.23). |
| `test_paperless_liveness_cannot_kill_a_booting_pod.py` | Paperless had no startupProbe, and liveness at 60 s + 3x30 s killed every fresh container ~150 s in, before its first boot finished: 7 of 7 rollouts 10-29..11-06 restarted once, so each Recreate rollout was a 4-5 min outage. The same probe stretched quo's ~150 s stall (11-05) into 7 min of 503s and a page (infra-tjscj). |

## Two design rules, both learned the hard way

**⛔ An empty selection is not a pass.** Every test asserts it actually found a population
before asserting the population is clean. Without that, a discovery bug — a renamed field,
a glob that stopped matching — turns the test green while it checks nothing. This is the
same failure as a backup report that passes because it found no volumes to check.

**⛔ A grep hit is not a use.** `ctf/fleet.yaml` mentions `runStrategy` in a comment
explaining that it must never be excluded; a substring search flags it as a violation. The
runStrategy test therefore parses the actual excluded JSON pointers, and the image tests
parse each YAML document rather than grepping for `image:`.

## Adding one

1. Write the incident at the top of the file, in enough detail that someone who was not
   there can tell whether a future failure is the same thing.
2. Assert the population is non-empty, then assert it is clean.
3. **Prove it fails.** Introduce the violation, watch the test go red, then revert. A test
   that has never failed proves nothing — every test here was poison-tested before it
   shipped. For the two image tests the poison is cheap: strip an `@sha256` digest from a
   floating image and watch the floating test go red.

Discovery uses `git ls-files`, so an untracked new manifest is invisible: `git add` before
trusting a local run.
