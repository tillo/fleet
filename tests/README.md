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
| `test_helm_chart_bundles_pin_a_version.py` | An unpinned `helm.chart` resolves to whatever the repo calls latest at bundle time, so the deployed version moves with no commit. `keel/fleet.yml` records exactly this ("Was: unpinned"). |
| `test_keel_pollschedule_is_seconds_first.py` | `keel.sh/pollSchedule` is **seconds-first**: a normal 5-field crontab is accepted and silently means something else, and an unparseable value falls back to `@every 4h` with no warning. |
| `test_kubevirt_comparepatch_keeps_runstrategy.py` | Excluding `/spec/runStrategy` from a VM's Fleet diff hides the VM being in the wrong power state, and the bundle sits NotReady forever with no clue why. |
| `test_alert_rules_carry_a_human_summary.py` | A page whose text does not say what happened costs a diagnosis round-trip. Currently true of all 488 rules — which is exactly when it is cheap to lock in. |

## Two design rules, both learned the hard way

**⛔ An empty selection is not a pass.** Every test asserts it actually found a population
before asserting the population is clean. Without that, a discovery bug — a renamed field,
a glob that stopped matching — turns the test green while it checks nothing. This is the
same failure as a backup report that passes because it found no volumes to check.

**⛔ A grep hit is not a use.** `ctf/fleet.yaml` mentions `runStrategy` in a comment
explaining that it must never be excluded; a substring search flags it as a violation. The
runStrategy test therefore parses the actual excluded JSON pointers, and the keel test
skips comment lines.

## Adding one

1. Write the incident at the top of the file, in enough detail that someone who was not
   there can tell whether a future failure is the same thing.
2. Assert the population is non-empty, then assert it is clean.
3. **Prove it fails.** Introduce the violation, watch the test go red, then revert. A test
   that has never failed proves nothing — all five here were poison-tested before they
   shipped.

Discovery uses `git ls-files`, so an untracked new manifest is invisible: `git add` before
trusting a local run.
