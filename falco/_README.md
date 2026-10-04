# falco — runtime security detection (PHASE 1: observe only)

Falco watches syscalls on every node through an eBPF probe and reports when
something unexpected executes inside a running container. It is the detection
layer the estate did not have: ModSecurity blocks at the edge, the NetworkPolicy
programme (beads `infra-az8.4`) prevents, trapeye deceives, and Rancher Logging
records — but nothing answered *"did something unexpected just run in there?"*.

Design study, with every measurement this bundle relies on:
`~/studies/falco-integration-design-20260922.md`.

## Why this fits here at all

Kernel **6.12.0-160000.28-default** on all three nodes, with
`/sys/kernel/btf/vmlinux` present and `CONFIG_DEBUG_INFO_BTF=y`. That means the
**modern eBPF (CO-RE) driver works with no kernel module**.

That is the whole argument. Compare `cephfs-kmod/`, which exists solely because
a module had to be built from kernel-source and rebuilt after every kernel bump.
A second out-of-tree module on these nodes would have been hard to justify;
`modern_ebpf` avoids the entire class of problem.

## What phase 1 deliberately does NOT do

| Not here | Why |
|---|---|
| Alerting (`PrometheusRule`) | A threshold invented before the first measurement is a guess. Baseline first. |
| Dedicated OpenObserve output | Would need an ingest credential on three privileged pods. Falco's JSON stdout is already collected by `rancher-logging-root-fluentbit`, so events reach o2 anyway. |
| Custom rule exceptions | We do not yet know what fires. Writing exceptions first would suppress the very evidence phase 2 needs. |
| `k8saudit` source | Structurally half-blind here — see below. |
| Falco Talon (auto-response) | Response automation on a detector nobody has learned to trust yet is how you cause your own outage. |

## Settings that are not the chart default, and why

- **`driver.kind: modern_ebpf`** with **`leastPrivileged: true`** — no kernel
  module, and no `privileged: true`; just CAP_BPF, CAP_PERFMON, CAP_SYS_RESOURCE,
  CAP_SYS_PTRACE. Confirmed in the rendered DaemonSet.
- **`collectors.containerEngine.engines.cri.sockets`** pinned to
  `/run/k3s/containerd/containerd.sock`. `/run/containerd/containerd.sock` does
  **not** exist on these nodes. Chart 9.2.0 happens to list the k3s path among
  its CRI defaults, so this would probably work unpinned — but it would work by
  probe order, and the failure mode is silent: Falco keeps running and every
  event loses its pod, namespace and image.
- **`falcoctl.artifact.follow.enabled: false`** — the chart default is `true`,
  which re-pulls `falco-rules:5` from ghcr.io every 168h and swaps the ruleset
  under a running Falco. That is a floating dependency inside a pinned,
  git-reviewed bundle: detection behaviour would change with no MR and no way to
  say which rules were live when an event fired. Rules change when git says so.
- **`falco.priority: notice`** — the chart default is `debug`, a firehose on a
  KubeVirt cluster, and o2 already uses ~19.1 GB/day of a 50 GB/day ceiling.
- **`falco.syscall_event_drops`** with `log` + `alert` — dropped syscalls are
  blind windows, not a performance curiosity.
- **`image.registry` is NOT set to zot.mdapi.ch**, against the estate habit.
  zot proxies docker.io through an explicit prefix allow-list and
  `falcosecurity` is not in it (verified: 0 hits against a control returning 1,
  and no catch-all wildcard), so pointing at the mirror would give
  ImagePullBackOff on all three nodes. Adding those prefixes — and the two other
  registry keys the chart needs — is a phase-2 follow-up, deliberately kept out
  of the bundle that first brings Falco up.

## Cost, stated plainly

~512Mi requested x 3 nodes (1Gi limit), plus two small sidekick replicas. The
cluster sits at 70/76/80% memory with swap 0 and a standing ~10 GiB N-1
shortfall, so Falco consumes roughly **15-20% of the gap** that programme is
closing. Booked deliberately, not discovered later.

Priority class is left unset: `mdapi-default` is the cluster's GLOBAL-DEFAULT
and Falco inherits it. **Do not move Falco to `mdapi-low`** — a detector evicted
under memory pressure is absent exactly when the cluster is behaving strangely.

## The k8saudit question (do not enable casually)

Harvester supplies the API-server audit policy
(`/etc/rancher/rke2/config.yaml.d/92-harvester-kube-audit-policy.yaml`) and it is
`level: Metadata`, verbs `create/delete/patch`, omitting `ResponseStarted` and
`ResponseComplete`. Consequences:

1. No request bodies — every rule keyed on `ka.req.*` (*Create Privileged Pod*,
   *Create HostPath Mount*, *Create Sensitive Mount*) **cannot fire**.
2. No `get`/`list`/`watch` — detecting **secret reads** is impossible.
3. No `ka.response.code` — "unauthorized attempt" rules break.

Raising it means editing a Harvester-managed control-plane file on an estate
where RKE2 is gated by the Harvester version. An enabled-but-blind source is
worse than an absent one, because it looks like coverage.

(The related audit-log gap — quo's log being written and collected by nobody —
was fixed separately in `monitoring-logging/addon.yml`, 2026-09-22.)

## Phases

| Phase | Work | Gate |
|---|---|---|
| 1 ✅ | Bundle, DaemonSet, Falco metrics, no alerting | done 2026-09-22 — 3/3, modern_ebpf, no kernel module |
| 2 ✅ | Exception ruleset from what actually fired | done 2026-09-22 — CRITICAL rate 1,330/15m → **0**, other rules still firing |
| 3 ✅ | Health + novelty alerts, dashboard, CRITICAL alert armed | done 2026-09-22 — each alert poison-tested by inverting it |
| 4 ⛔ | **k8saudit — DECIDED NO** (2026-09-22) | revisit only if Harvester makes the audit policy a supported setting |
| 5 ⛔ | **Talon auto-response — DECIDED NO** (2026-09-22) | revisit after a quarter of operated, tuned detection |

### Phase 4 — why k8saudit is a No

Enabling it would need Harvester's audit policy raised from `level: Metadata`
to `Request`, and `ResponseComplete` un-omitted. Three reasons not to:

1. **The file is Harvester's.** `/etc/rancher/rke2/config.yaml.d/92-harvester-kube-audit-policy.yaml`
   is managed by the platform, on all three control-plane nodes. RKE2 here is
   gated by the Harvester version, and a malformed audit policy does not
   degrade — it stops kube-apiserver from starting.
2. **It would not survive.** A Harvester upgrade re-seeds that file, so the
   change is not durable and its loss would be silent — k8saudit would quietly
   go blind again while still looking enabled.
3. **`Request` level logs request bodies**, multiplying audit volume on a
   pipeline that only just reached all three nodes (see the 2026-09-22 quo fix
   in `monitoring-logging/`).

⇒ Until then, **leave the source off**. An enabled-but-blind source is worse
than an absent one, because it looks like coverage: every `ka.req.*` rule
cannot fire, secret reads are invisible, and there are no response codes.

### Phase 5 — why Talon is a No

Response automation is only as good as the detection it trusts, and this
detector is days old. The estate already made the same call for NetworkPolicy —
auto-generated policy was rejected in favour of hand authorship — and the
reasoning carries: a wrong automated response is an outage you caused yourself,
during an incident you were already having.

Revisit when the phase-2 exception set has been stable for a quarter and
`FalcoNewRuleFired` has stopped being interesting.

## Where to follow this up

**Trends and health** — Grafana, folder *Security*, dashboard
**Runtime detection — what ran, and whether every node was watching** (`sec-falco-runtime`, shipped in
`fleet/grafana-extra-dashboards/falco-dashboard.yml`). Backed by
`falcosecurity_falco_rules_matches_total{rule_name,priority}` in VictoriaMetrics.

**Alerts** — `fleet/monitoring-rules/55-falco.yml`: node coverage, silence,
syscall drops, new-rule novelty, and two ruleset-hash rules.

**Individual events** — OpenObserve, stream `pod_logs`, filter
`k8s_namespace = 'falco'`.
⛔ `pod_logs` has **no full-text index**, so `match_all()` returns nothing and
looks exactly like "no events". Use `_raw LIKE '%Drop and execute new binary%'`.
⛔ The namespace field is `k8s_namespace`. `kubernetes_namespace_name` also
exists in the schema and is **always empty** — querying it returns a convincing
zero.

### ⛔ falcosidekick metrics are NOT scraped

`serviceMonitor.create: true` covers **Falco only**; falcosidekick has its own
separate toggle which is off, so `falcosidekick_falco_events_total` does not
exist on this cluster (verified: 0 series). Anything written against that metric
name will never fire. Use `falcosecurity_falco_rules_matches_total` instead, or
enable the sidekick's ServiceMonitor first.

### What the metrics cannot tell you

`falcosecurity_falco_rules_matches_total` carries `rule_name` and `priority`, but
its only Kubernetes labels are **Falco's own pod**. There is no label for the
workload that caused the event. Attribution exists only in the events, which is
why both surfaces are needed rather than one.

⛔ When phase 3 adds the ExternalSecret for the o2 credential, it must go in a
**raw sibling bundle** (e.g. `falco-secrets/`), not in this directory: a Fleet
Helm bundle silently ignores loose manifests while still reporting Ready.

## Expected noise (phase 2 candidates, not yet excepted)

Predicted from what these nodes actually run — to be confirmed against real
events, not assumed:

- **jump-bastion** runs `apt` at entrypoint, so every pod roll looks like
  package management in a container. If this fires, except it early: an
  integration that pages you about your own jumphost gets muted, and a muted
  detector is worse than none.
- **cephfs-kmod-builder** compiles and `insmod`s `ceph.ko` on all three nodes.
- **virt-handler / virt-launcher** (KubeVirt) manipulate namespaces by design.
- **longhorn**, **harvester-node-disk-manager**, **rke2-canal**, **multus**,
  **fluentbit**, **trapeye**.

Scope the rules, never disable them — `insmod` outside cephfs-kmod should still
be loud.

## Verify by behaviour, not by Ready

```sh
kubectl -n falco get ds falco                     # 3/3
kubectl -n falco exec ds/falco -- falco --version # reports modern_ebpf
lsmod | grep falco                                # MUST be empty
```

Then trigger a real detection (`cat /etc/shadow` in a scratch pod) and confirm
the event carries `k8s.pod.name` and `k8s.ns.name`, not just a PID. A pod being
`Running` proves nothing about whether the probe is attached.
