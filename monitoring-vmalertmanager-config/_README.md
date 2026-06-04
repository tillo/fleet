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

- **`Watchdog`** (kube-prometheus-stack dead-man's switch) is evaluated by
  the bundled `rancher-monitoring-prometheus`, not vmalert. Its
  healthchecks.io ping AlertmanagerConfig stays in
  `fleet/monitoring-alertmanager-config/`.
- **All other chart-bundled / vendor alerts** (KubeNodeNotReady,
  KubePodCrashLooping, NodeDiskRunningFull, etc.) are evaluated by
  bundled Prometheus and delivered by bundled Alertmanager via its
  chart-default Pushover receiver.

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
