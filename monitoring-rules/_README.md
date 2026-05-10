# monitoring-rules

App-level PrometheusRule + (later) AlertmanagerConfig CRDs that the
existing rancher-monitoring stack picks up automatically (its operator
selectors are `{}` for SM/PM/Rule/AlertmanagerConfig and a label match
on Probe).

## Conventions

Every alert MUST carry these labels so AM routing and the statuspage
adapter know what to do:

| Label | Values | Purpose |
|-------|--------|---------|
| `severity` | `critical`, `warning`, `info` | Maps to statuspage status: critical→major_outage, warning→degraded_performance |
| `component` | `Mail`, `Identity`, `GitLab`, `Files`, `Notes`, `Websites`, `DNS` | Maps to statuspage component_id (see memory `reference_statuspage_components`) |
| `team` | `mdapi` | Distinguishes our alerts from rancher-monitoring's defaults so AM routes can scope cleanly |

Annotations that should always be set:
- `summary` — short, one-line, includes `{{ $labels.namespace }}/{{ $labels.deployment_or_pod }}`
- `description` — longer; what it means and what to check

## Files

- `00-namespace.yml` — the `monitoring` namespace
- `01-mail.yml` … `07-dns.yml` — one PrometheusRule per statuspage component

## What this bundle does NOT do

- No AlertmanagerConfig yet (added in `monitoring-alertmanager-config` bundle)
- No receivers wired (Pushover / statuspage adapter come later)
- No exporter-specific alerts (postfix-exporter, blackbox come in their own bundles)

So pushing this bundle alone changes nothing visible — the alerts will load
into Prometheus's `/alerts` page but won't notify anyone yet.
