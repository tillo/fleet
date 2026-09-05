# monitoring-logging

Step 5 (rancher-logging install + Cribl Output) is GitOps-managed in this
bundle. Step 6 (the Cribl pipeline → Alertmanager webhook) is
configured in the Cribl UI because Cribl pipelines aren't fleet-friendly.

## Step 6 — manual Cribl configuration

Once `rancher-logging` is healthy and `mdapi-fluentd` starts shipping, do
this in the Cribl UI (https://cribl.mdapi.ch):

### 6.1 — Add an HTTP/REST source

- Sources → HTTP/Bulk API → New
- Port: `8080`
- Address: `0.0.0.0`
- Auth tokens: none (cluster-internal traffic only; the Service is `ClusterIP`)
- Save & enable

### 6.2 — Add an Alertmanager webhook destination

- Destinations → Webhook → New
- Endpoint URL: `http://rancher-monitoring-alertmanager.cattle-monitoring-system.svc.cluster.local:9093/api/v2/alerts`
- Method: `POST`
- Format: `JSON Array`
- Send a single event per request: **off** (Alertmanager accepts arrays)

### 6.3 — Pipeline `postfix-failures-to-am`

Route only events where `signal === 'postfix_failure'` (set by the
`record_modifier` filter in `flow-postfix.yml`) into this pipeline.

Functions in the pipeline:
1. **Eval** — build the Alertmanager alert payload as a single nested object:
   ```javascript
   __alert = {
     labels: {
       alertname: 'PostfixDeliveryFailure',
       team: 'mdapi',
       component: 'Mail',
       severity: 'warning',
       namespace: 'mail',
       signal: 'postfix_failure'
     },
     annotations: {
       summary: 'postfix log error: ' + (log || message || '').slice(0, 120),
       description: 'Cribl matched a postfix failure pattern in mail.log; auto-resolves 5min after last hit.'
     },
     // 5 minutes from now; Cribl extends this on every new event so the
     // alert stays firing while errors keep landing.
     endsAt: new Date(Date.now() + 5*60*1000).toISOString()
   }
   ```
2. **Eval → Out → Drop** the rest of the original event fields (only `__alert` survives).
3. **Eval → Out** rename `__alert` to root `_raw` so destination sends just the alert object as a JSON array.

### 6.4 — Connect & test

- Route: `signal=='postfix_failure'` → pipeline `postfix-failures-to-am` → destination `alertmanager-webhook`
- Test by exec'ing into a docker-mailserver pod and writing a fake line:
  `kubectl -n mail exec deploy/docker-mailserver -- bash -c 'logger -t postfix/smtp "test fatal: synthetic loops back to myself"'`
- Within ~10s the AM `/alerts` page should show `PostfixDeliveryFailure` (or a
  near-name); it auto-resolves 5min after the last test event.

### Why this and not a postfix_exporter sidecar?

The exporter approach (parse mail.log → Prometheus metrics → AM rule) is
cleaner conceptually but requires either a sidecar in the docker-mailserver
pod (chart-managed, awkward) or a sidecar pod that mounts the same RWO log
PVC (impossible — RWO can't be shared). Cribl is already deployed and
chewing logs anyway, so adding one routing rule is the lowest-friction
path. If we ever want native metrics, we can move to a sidecar later
without touching anything else.
