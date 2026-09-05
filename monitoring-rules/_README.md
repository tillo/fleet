# monitoring-rules

App-level `PrometheusRule` CRDs for the **mdapi** monitoring stack. The VictoriaMetrics
operator converts every PrometheusRule carrying `monitoring-stack: mdapi` into a VMRule;
vmalert evaluates them; alerts route through the VMAlertmanager config in
`../monitoring-vmalertmanager-config/`.

Rewritten 2026-07-06 (fleet+vmalert cleanup — see `~/fleet-monitoring-study/PROPOSAL.md`).
Conventions here are enforced by **Check D in `../lint.py`** (runs in fleet CI).

## Routing — read this before adding an alert

The parent route is a **deliberate black-hole**: an alert that matches no child route is
evaluated, fires, and is silently dropped. The child routes match `team="mdapi"` and tier by
severity:

| severity | Pushover priority | repeat |
|---|---|---|
| `critical` | 1 (interrupts) | 4h |
| `warning` / `info` | −1 (in-app, silent) | 7d |

The deadman (`WatchdogMdapi`, `team: mdapi-watchdog`, `severity: none`) has its own VMAC route.
**History:** 94 alerts — the entire imported Ceph pack among them — carried no `team` label and
notified nobody until 2026-07-06 (the 07-04 qui wedge evicted 5 Ceph OSDs without a single
Ceph page). That is why the label contract below is lint-enforced.

## Label contract (every alert)

| Label | Required value | Purpose |
|---|---|---|
| `team` | `mdapi` (`mdapi-watchdog` for the deadman only) | escapes the black-hole |
| `severity` | `critical` \| `warning` \| `info` | Pushover tier + statuspage status |
| `component` | one of the taxonomy below | Alertmanager grouping + statuspage component |

Component taxonomy — **defined in
`../monitoring-statuspage-adapter/adapter-cm.yml`, and nowhere else**:

- `components.json` — the **public** components. An alert carrying one of these
  degrades mdapi.statuspage.io, visible to strangers.
- `INTERNAL_COMPONENTS` in the embedded `adapter.py` — the **private** ones. The
  adapter skips them by design; they exist only as a Pushover grouping key.

`lint.py` derives its `RULE_COMPONENTS` from those two and rejects any rule whose
`component:` label is outside the union, so adding a component is a one-place edit.
The list is deliberately not reproduced here: this file used to carry its own copy,
and until 2026-08-27 the estate had four hand-maintained copies of which two had
drifted (see the 2026-08-27 taxonomy-reconciliation commit). To read the current
set: `python3 -c "import yaml,json;d=[x for x in yaml.safe_load_all(open('monitoring-statuspage-adapter/adapter-cm.yml')) if x][0]['data'];print(sorted(json.loads(d['components.json'])))"`

Exceptions (also encoded in lint): the three `SyntheticProbe*` alerts inherit `component`
per-target from the blackbox Probe `targetLabels` — do NOT add a static component there
(rule labels override series labels and would clobber the per-target value).

Annotations: `summary` (one line, include the failing object), `description` (what it means,
what to check, and the runbook/memory pointer when one exists).

## File numbering

- `NN-topic.yml`, two digits; take the **next free number** for a new topic.
- `NNa-`, `NNb-` letter suffixes are for a **related sub-series** of NN only
  (e.g. `20-longhorn-disk-capacity` / `20a-pvc-growth`).
- Never reuse a freed number; collisions fail CI (lint `RULES-NUMBERING`).

| Range in use | Theme (grown, not designed — bands are not enforced) |
|---|---|
| 01–08 | statuspage components / user-facing services |
| 09–17 | probes, logging pipeline*, monitor-the-monitoring, watchdog |
| 18–25 | nodes, storage (Longhorn/PV), router |
| 26–31 | platform services (OpenBao, certs, ESO), Ceph, VolSync |

(*) The logging ClusterFlow/ClusterOutput CRs moved to the `monitoring-logging-flows`
bundle (2026-07-06). The blackbox Probe files (`10`, `17`) STAY here by design: the
monitoring-blackbox bundle is an external-chart bundle (raw manifests beside it are ignored
by Fleet), and the Probes belong next to the `11-blackbox-alerts` that fire on their metrics.
15b keeps its ServiceMonitor for the same external-chart reason. So: this bundle =
PrometheusRules + the Probe targets they alert on.

## Adding an alert — checklist

1. Pick the file by topic (or a new `NN-topic.yml` with the next free number).
2. Labels per the contract above; severity honestly (`critical` buzzes the phone).
3. `python3 ../lint.py` locally, or let CI catch it.
4. Windmill-pushed metrics: the generic `BatchJobStale` in `12-batch-jobs.yml` already covers
   staleness of any job that pushes `<job>_last_run_timestamp_seconds` — don't add per-job
   staleness rules. Business rules go in a per-script group `app-batch-jobs.<name>`.
