# monitoring-alertmanager-config

Post-Phase-4 this bundle is the minimal glue that still wires the
bundled `rancher-monitoring-alertmanager` to *one* external endpoint:
healthchecks.io for the `Watchdog` dead-man's switch.

The team=mdapi routing previously here moved to
`fleet/monitoring-vmalertmanager-config/` (VMAlertmanagerConfig consumed
by `vmalertmanager-mdapi`).

## What still lives here

- **`watchdog-deadman` AlertmanagerConfig** — routes the always-firing
  `Watchdog` alert from bundled Prom to a healthchecks.io HEAD ping.
  Watchdog is a kube-prometheus-stack vendor rule (no `mdapi` label) so
  it's evaluated by bundled Prometheus, not by vmalert. Keeping it here
  means the dead-man path is wholly independent of the VM stack — if
  vmalert / vmalertmanager are themselves dead, healthchecks.io still
  catches it.

- **`healthchecks-watchdog` ExternalSecret** — supplies the
  `https://hc-ping.com/<uuid>` URL for the above (key
  `/mdapi/pushover/healthchecks-watchdog-url` in akeyless).

- **`pushover` ExternalSecret** — the shared Pushover user-key + token,
  consumed by `vmalertmanager-mdapi`'s VMAlertmanagerConfig in the
  sibling bundle. Left in this bundle for now because moving an ES
  across bundles deletes the underlying Secret and triggers a brief
  delivery gap; safer to leave it where it is.

## Bundled-AM matcher strategy quirk

`alertmanagerConfigMatcherStrategy=None` must stay on
`rancher-monitoring-alertmanager`. The default `OnNamespace` would
prepend `namespace=monitoring` to the `watchdog-deadman` route and
prevent the Watchdog alert (no `namespace` label) from matching. Patched
by hand on 2026-05-10; Rancher chart upgrades may revert it:

```
kubectl --context mdapi-prod -n cattle-monitoring-system patch \
  alertmanager rancher-monitoring-alertmanager --type=merge \
  -p '{"spec":{"alertmanagerConfigMatcherStrategy":{"type":"None"}}}'
```

## Receivers (this bundle)

| Receiver | Notifies | Purpose |
|---|---|---|
| `healthchecks-watchdog` | webhook (hc-ping.com) | `Watchdog` dead-man heartbeat |
