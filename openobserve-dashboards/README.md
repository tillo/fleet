# OpenObserve dashboards

Exported JSON for dashboards living in OpenObserve at `logs.mdapi.ch`. Fleet
does **not** apply these — they're source-of-truth artifacts. If a dashboard
is lost, re-import via the OO UI ("Import" button on the dashboards page) or
POST to `/api/default/dashboards` with the JSON as the body.

## Files

| File | Source stream | OO URL |
|------|---------------|--------|
| `technitium-queries.json` | `technitium_queries` (fed by Windmill `f/infra_health/technitium_query_log_to_o2`) | https://logs.mdapi.ch/web/dashboards/view?org_identifier=default&dashboard=7461167006352408576 |

## Re-importing after a wipe

```bash
OO_PASS=$(akeyless get-secret-value -n /mdapi/openobserve/o2/root-password)
curl -u "tillo@tillo.ch:${OO_PASS}" \
  -X POST -H 'Content-Type: application/json' \
  https://logs.mdapi.ch/api/default/dashboards \
  -d @technitium-queries.json
```

After re-import the `dashboardId` will be reassigned. Update the Grafana
cross-link in `fleet/monitoring-dashboards/technitium.yml` with the new ID.
