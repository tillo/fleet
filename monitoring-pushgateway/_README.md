# monitoring-pushgateway

Step 7 — landing pad for short-lived job metrics. Replaces the
"Windmill script fires Pushover directly" pattern with the Prometheus-
native flow:

```
batch script ──push──▶ Pushgateway ──scrape──▶ Prometheus ──rule──▶ Alertmanager
                       (HTTP)         (15s)              (PromQL)    (statuspage + Pushover)
```

## Migration pattern

For each Windmill script being migrated:

1. **Replace** the script's direct Pushover POST with a metric push:
   ```python
   import time, urllib.request
   metrics = f'''
   gitlab_backup_age_seconds {time.time() - last_backup_ts}
   gitlab_backup_size_bytes {size}
   gitlab_backup_health_last_run_timestamp_seconds {time.time()}
   gitlab_backup_health_last_run_success {1 if success else 0}
   '''
   urllib.request.urlopen(
       'http://pushgateway.monitoring.svc.cluster.local:9091/metrics/job/gitlab_backup_health',
       data=metrics.strip().encode(),
       method='POST',
   )
   ```
2. **Add** a `PrometheusRule` to `monitoring-rules/` that alerts on the
   pushed gauges with the right `severity` + `component` + `team=mdapi`
   labels:
   ```yaml
   - alert: GitLabBackupStale
     expr: gitlab_backup_age_seconds > 36 * 3600
     labels: {severity: warning, component: GitLab, team: mdapi}
   ```
3. **Delete** the Pushover POST + the alerting logic from the script.

The included `app-batch-jobs.staleness` rules act as a backstop: if any
script stops pushing for >24h, a generic `BatchJobStale` fires so we
catch a silently-disabled script.

## Migration order (smallest blast radius first)

1. `rancher_backup_check` — single signal (snapshot age)
2. `rackspace_spend` — single signal (monthly $ proj)
3. `gitlab_backup_health` — handful of signals (backup age, runner pod count)
4. `pv_reclaim_policy_analysis` — informational
5. `bpir4_health` — multiple signals; needs the BPI-R4 SSH wrapper to push
6. `storage_health` — TrueNAS pool fill + Synology RAID; one push per host
7. `weekly_infra_health` — eventually delete; everything it aggregates
   is now its own rule

`frigate_io_watchdog` ACTS on issues (pod restart) — keep as cron, but
have it also push `frigate_io_watchdog_last_run_*` for the staleness check.

## Why Pushgateway and not direct ServiceMonitor?

Short-lived jobs (Windmill scripts, CronJobs) start, do work, exit. Their
HTTP `/metrics` endpoint isn't around when the next 15s scrape happens.
Pushgateway holds the last-pushed values until either the script pushes
again or you DELETE the job (`DELETE /metrics/job/<name>`).
