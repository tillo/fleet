# monitoring-vmalertmanager-config

VMAlertmanagerConfig CRs consumed by `vmalertmanager-mdapi` in the
`monitoring` namespace (the dedicated VM-stack Alertmanager).

## What routes here

Only alerts evaluated by `vmalert-mdapi`, which (after Phase 4) selects
exclusively PrometheusRule / VMRule CRs labeled `monitoring-stack: mdapi`.
That maps to everything in `fleet/monitoring-rules/`.

These alerts carry `team=mdapi` and route to:
- the statuspage adapter webhook (component health updates), and
- Pushover (phone notification + Grafana deep-link).

## What does NOT route here

⚠️ **Verified 2026-09-25: there is no bundled Prometheus or Alertmanager any
more.** `cattle-monitoring-system` runs only kube-state-metrics, node-exporter
and the operator (they feed vmagent); `kubectl get prometheuses,alertmanagers -A`
returns nothing. The secret `alertmanager-rancher-monitoring-alertmanager`, whose
default receiver is Pushover, is a LEFTOVER that nothing reads, and so is the
AlertmanagerConfig in `monitoring-rancher-am-config/`. The two bullets below
describe the pre-cutover layout — do not build on them:

- ~~`Watchdog` is evaluated by the bundled `rancher-monitoring-prometheus`~~ —
  the live dead-man is `WatchdogMdapi` → `watchdog-deadman-vmac.yml`.
- ~~Vendor alerts (KubeNodeNotReady, KubePodCrashLooping …) are delivered by the
  bundled Alertmanager via its chart-default Pushover receiver~~ — nothing
  evaluates them there; vmalert is the only rule engine that pages.

## OpenObserve shadow (2026-09-25)

`o2-incidents-vmac.yml` copies every team=mdapi alert to the OpenObserve External
Alert Source `vmalertmanager` (no incident destination): a week of measurement
before deciding whether O2 becomes the correlating hop in front of Pushover.
Details and the auto-resolve interaction are in that file's header.

## Selector glue

`vmalertmanager-mdapi.spec.configSelector` matches
`monitoring-stack: mdapi`. CRs in this bundle carry that label so the
operator merges them into the generated `alertmanager.yaml`.

## Pushover secret

The `pushover` ExternalSecret lives in this bundle (`pushover-es.yml`)
since vmalertmanager-mdapi is now the only consumer. `creationPolicy:
Merge` so accidental bundle removal doesn't drop the Secret out from
under any race; the cost is an orphan Secret if the entire bundle is
ever deleted, which is the correct trade-off here.
