# monitoring-alertmanager-config

Wires the existing rancher-monitoring Alertmanager to my receivers
(statuspage adapter + Pushover) for alerts labelled `team=mdapi`.

## How the routing composes

The Prometheus Operator merges all `AlertmanagerConfig` CRDs into one
Alertmanager config alongside the rancher-monitoring chart's hardcoded
default config (a single Pushover receiver named `default`).

For our routes to match alerts from any namespace (mail, joplin, …), the
Alertmanager CR's `alertmanagerConfigMatcherStrategy` MUST be `None`. The
default is `OnNamespace`, which would prepend `namespace=monitoring` to
every route from this AC and exclude all real workload alerts.

Strategy was patched to `None` by hand on 2026-05-10. Rancher chart
upgrades may revert it; if alerts mysteriously stop reaching statuspage
after a Rancher upgrade, re-apply:

```
kubectl --context mdapi-prod -n cattle-monitoring-system patch \
  alertmanager rancher-monitoring-alertmanager --type=merge \
  -p '{"spec":{"alertmanagerConfigMatcherStrategy":{"type":"None"}}}'
```

## What this bundle does NOT replace

The chart-managed default Pushover receiver still exists. We don't touch it
because it sits inside a Secret owned by the rancher-monitoring helm release.
For team=mdapi alerts we deliver the same Pushover notification ourselves
(via this AC's pushoverConfig) so we don't depend on the chart's default —
this way the rancher receiver can be retired in the future without breaking
our mdapi alerts.

## Receivers

| Receiver | Notifies | Purpose |
|---|---|---|
| `statuspage-and-pushover` | webhook (statuspage adapter) + Pushover | All team=mdapi alerts |
