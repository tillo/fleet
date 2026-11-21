# Technitium DHCP Failover Runbook

Technitium does **not** implement the DHCPv4 failover protocol (RFC 3074).
Only one pod can answer DHCP at a time — the secondary is a cold standby for
DHCP, even though it is a hot standby for DNS.

The `technitium-dhcp` LoadBalancer Service (192.168.1.55:67/UDP) has selector
`app=technitium-primary`. When the primary pod is unhealthy, this runbook
moves DHCP to the secondary.

When you reach this runbook, alert `TechnitiumDhcpNoEndpoints` is firing.

## What breaks vs what holds

| Effect | When |
|---|---|
| New DHCPDISCOVER / RENEW get no answer | Immediately on primary loss |
| Existing leases keep working | Until `T1` (≈ leaseTime/2) |
| Static reservations break for new boots | Immediately |
| DNS resolution unaffected | DNS service routes to both pods |

LAN lease times in our config: 12h, so you have **~6h** until the first
renewals start failing visibly. Run the failover within that window.

## Prerequisites

```bash
kubectx mdapi-prod
kubens technitium
```

You need: kubectl, working `mdapi-prod` context. No Akeyless needed for the
mechanical cutover; the secondary already has DHCP scopes pre-loaded from
configuration (see Step 3 verification).

## Step 1 — Confirm the primary is actually down

Don't fail over a flaky primary; you'll just split-brain the lease DB.

```bash
kubectl -n technitium get pod -l app=technitium-primary -o wide
kubectl -n technitium get endpoints technitium-dhcp
kubectl -n technitium logs -l app=technitium-primary --tail=50
```

Decide:
- **Primary crash-looping / OOMKilled** → continue, the lease DB on its PVC
  is still intact and we'll reuse it via PV swap (Step 2b).
- **Primary node down (qui offline)** → continue, but the PVC is also stuck
  until the node returns. You may have to skip Step 2 and accept a fresh
  leases DB on the secondary (DHCP DISCOVER works fine, RENEW from existing
  clients will be NAK'd → they re-DISCOVER → fine after ~30s flap).
- **Primary intermittently healthy** → don't fail over. Fix the root cause.

## Step 2 — Move the leases DB to the secondary

Two paths. Pick **2a** if the primary PVC is accessible.

### 2a — Live copy via debug pod (PREFERRED — keeps leases intact)

Scale the primary down to release the file lock on the SQLite leases DB:

```bash
kubectl -n technitium scale deploy technitium-primary --replicas=0
kubectl -n technitium wait --for=delete pod -l app=technitium-primary --timeout=60s
```

Spawn a debug pod with both PVCs mounted:

```bash
kubectl -n technitium apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: dhcp-failover-copy
  namespace: technitium
spec:
  restartPolicy: Never
  containers:
  - name: copy
    image: alpine:3.20
    command: ["/bin/sh","-c","sleep 600"]
    volumeMounts:
    - { name: pri, mountPath: /pri }
    - { name: sec, mountPath: /sec }
  volumes:
  - name: pri
    persistentVolumeClaim:
      claimName: technitium-primary-data
  - name: sec
    persistentVolumeClaim:
      claimName: technitium-secondary-data
EOF
kubectl -n technitium wait --for=condition=Ready pod/dhcp-failover-copy --timeout=60s

# Stop the secondary too so we can write to its data dir cleanly
kubectl -n technitium scale deploy technitium-secondary --replicas=0
kubectl -n technitium wait --for=delete pod -l app=technitium-secondary --timeout=60s

kubectl -n technitium exec dhcp-failover-copy -- sh -c '
  cp -av /pri/config/dhcp /sec/config/dhcp.failover-staged
  # Snapshot any prior state on the secondary, then atomic swap
  [ -d /sec/config/dhcp ] && mv /sec/config/dhcp /sec/config/dhcp.before-failover-$(date +%s)
  mv /sec/config/dhcp.failover-staged /sec/config/dhcp
  ls -la /sec/config/dhcp
'

kubectl -n technitium delete pod dhcp-failover-copy --wait=false
kubectl -n technitium scale deploy technitium-secondary --replicas=1
kubectl -n technitium wait --for=condition=Ready pod -l app=technitium-secondary --timeout=120s
```

### 2b — Skip the copy (primary PVC unreachable)

The secondary already has its own (empty or stale) leases DB. New
DHCPDISCOVER clients will work immediately. Existing clients that try to
RENEW their old lease will get a NAK and re-DISCOVER within ~30s.

Just skip to Step 3.

## Step 3 — Enable DHCP scopes on the secondary

The secondary is normally configured with DHCP scopes *defined* but not
*enabled* (verify in the Technitium UI on https://dns2.mdapi.ch). If the
secondary doesn't have the scopes yet, you must add them now (UI → DHCP →
Scopes → enable each existing scope).

If scopes are pre-loaded but disabled:
```bash
# From the secondary's API (port-forward the UI service):
kubectl -n technitium port-forward svc/technitium-secondary 18553:5380 &
PF=$!
sleep 2

SEC_TOKEN=$(kubectl -n technitium get secret technitium-exporter-tokens \
  -o jsonpath='{.data.TECHNITIUM_API_SECONDARY_TOKEN}' | base64 -d)

# List scopes
curl -s "http://127.0.0.1:18553/api/dhcp/scopes/list?token=$SEC_TOKEN" | jq

# Enable each scope by name
for scope in lan dmz adlan; do
  curl -s "http://127.0.0.1:18553/api/dhcp/scopes/enable?token=$SEC_TOKEN&name=$scope" | jq .status
done

kill $PF
```

## Step 4 — Flip the LoadBalancer Service selector

```bash
kubectl -n technitium patch svc technitium-dhcp \
  --type=merge \
  -p '{"spec":{"selector":{"app":"technitium-secondary"}}}'
```

Verify endpoint is now the secondary pod IP:
```bash
kubectl -n technitium get endpoints technitium-dhcp
# Expected: 10.52.1.x:67 (secondary pod IP, not 10.52.0.x)
```

## Step 5 — Verify DHCP is answering

From a LAN host:
```bash
sudo nmap --script=broadcast-dhcp-discover -e <iface>
# Expected: offer from 192.168.1.55, your normal scope settings
```

Or watch a real client renew:
```bash
ssh root@bpi-r4 'logread -f | grep -i dhcp'
```

The alert `TechnitiumDhcpNoEndpoints` should clear within 3 minutes of
the selector flip.

## Step 6 — Recover the primary (when ready)

When the primary deployment is fixable:

```bash
# Make sure the primary's own DHCP scopes are still defined.
# If the failover used 2a, the leases DB on the primary is now stale
# (the secondary has been issuing leases). DO NOT just scale primary
# back up — you'll split-brain.

# Option A: cold swap back (downtime ~2min, restores original layout)
kubectl -n technitium scale deploy technitium-secondary --replicas=0
kubectl -n technitium wait --for=delete pod -l app=technitium-secondary --timeout=60s

# Copy current leases DB from secondary -> primary (reverse of Step 2a)
# ... (debug pod, same shape) ...

kubectl -n technitium scale deploy technitium-primary --replicas=1
kubectl -n technitium wait --for=condition=Ready pod -l app=technitium-primary --timeout=120s

# Flip the Service selector back
kubectl -n technitium patch svc technitium-dhcp \
  --type=merge \
  -p '{"spec":{"selector":{"app":"technitium-primary"}}}'

kubectl -n technitium scale deploy technitium-secondary --replicas=1

# Option B: leave it on the secondary indefinitely. Update the Helm
# release values to make secondary the DHCP primary, and the now-broken
# pod becomes the new secondary. This is the right move if the primary's
# node has a persistent fault.
```

## Reverting the Service selector change

Note: the patch is **not** sticky against Fleet/Helm reconciliation. The
chart manifest in `technitium-dhcp-svc.yml` still has
`selector: { app: technitium-primary }`. Fleet will eventually re-apply.

For a **prolonged** stay on the secondary, edit
`fleet/technitium/technitium-dhcp-svc.yml`, commit, and let Fleet apply.
Otherwise expect the selector to flip back the next reconcile — watch
the alert if you don't want that.

## What this runbook does NOT cover

- DHCPv6 / SLAAC (handled by router, not Technitium).
- Reservations sync: if you added a reservation only on the primary
  between backups, it's lost. Always add reservations via the UI on the
  pod that owns DHCP at the time.
- Auto-failover: explicitly out of scope. Technitium has no DHCP
  failover protocol and we have no Pacemaker. Plan accordingly.
