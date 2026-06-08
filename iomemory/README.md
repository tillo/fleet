# iomemory — FusionIO ioMemory fast tier (Harvester / Longhorn)

GitOps bundle that loads the **RemixVSL `iomemory-vsl`** kernel module on Gen8
nodes carrying a FusionIO ioMemory (VSL3) card, and exposes a Longhorn
low-latency StorageClass backed by those disks.

> **This bundle is INERT on commit.** The loader DaemonSet is gated by
> `nodeSelector: mdapi.ch/iomemory=enabled`, which no node has. The StorageClass
> has no matching disks yet. Nothing loads or schedules until you complete the
> activation runbook below, one node at a time.

## Contents
| File | Purpose |
|------|---------|
| `fleet.yaml` | prod-only target; namespace `iomemory-driver` (PSA=privileged) |
| `iomemory-ds.yml` | gated privileged DaemonSet that insmods the module |

The `zot-pull` image-pull secret for `iomemory-driver` is provided by the shared
`../zot-pull-secrets/` bundle (ES `zot-pull` → secret `zot-pull`, host
`zot.mdapi.ch`), per repo convention — not carried in this app bundle.

The `longhorn-iomemory` StorageClass (disk tag `iomemory`, dataLocality
best-effort) is **cluster-scoped**, so it lives in the sibling bundle
`../iomemory-storageclass/` (a `namespace:`-pinned bundle forbids cluster-scoped
objects — Fleet rejects them).

Loader **image** is built out-of-band from `~/iomemory-vsl-loader/` (its own
repo + CI, like the `gitlab-*-ee` images) and mirrored to
`zot.mdapi.ch/mdapi/iomemory-vsl-loader:<kernel-release>`.

## Why this shape
- **Driver:** RemixVSL `main` builds clean against Harvester's SLE16 6.12 kernel
  (compile-verified 2026-06-08, `-Werror`). Cards are VSL3 (~1.2 TB ioDrive).
- **Immutable host:** no toolchain on SLE Micro, so the `.ko` is prebuilt
  per-kernel in an image and just `insmod`-ed (NVIDIA-precompiled-driver pattern).
  vermagic must match → image tag == kernel release; rebuild on Harvester upgrade.
- **IOMMU:** the old "IOMMU group" blocker only applies to VM passthrough. Here
  the card is a **host disk**, so no passthrough and no ACS override needed.

## Prerequisites (do once, before any activation)
1. **Pull secret**: the `zot-pull` ExternalSecret for `iomemory-driver` lives in the
   shared `../zot-pull-secrets/` bundle and uses the existing shared Akeyless key
   `/mdapi/zot/zot-auth/password` — no new key needed. It syncs once cm/akeyless is
   up; a `zot-pull` secret was created manually to bridge the cm outage.
2. **Build + push the loader image** for the current node kernel
   (`6.12.0-160000.28-default`): see `~/iomemory-vsl-loader/README.md`. Result:
   `zot.mdapi.ch/mdapi/iomemory-vsl-loader:6.12.0-160000.28`.

## Activation runbook (one node at a time)
For a node `N` (start with the least critical; qua already has a card):
1. **Physical:** install a card (qui/quo). qua already has one.
2. **vfio:** if the card was set up for passthrough, remove its Harvester
   `PCIDeviceClaim` so it isn't re-bound to `vfio-pci` on boot. (The loader also
   unbinds at runtime, but the claim would re-grab it after a reboot.)
3. **Kernel cmdline (Intel):** ensure `iommu=pt` is on `N`'s cmdline
   (`cat /proc/cmdline`). If missing, add it via the node's bootloader/elemental
   config and **reboot drained**. ⚠️ Verify carefully — a bad boot config has
   bricked these nodes before. Non-fatal if absent, but recommended for DMA.
4. **Validate (the gate):** drain is not required for module load. Label the node:
   `kubectl label node N mdapi.ch/iomemory=enabled`
   Watch: `kubectl -n iomemory-driver logs -l app=iomemory-vsl-loader -f`
   Expect `fioinf ... ioDrive ... loading...` in dmesg and `/dev/fioa`
   (`fio-status -a`). If it fails, `kubectl label node N mdapi.ch/iomemory-`
   to back out — module load is reversible (`rmmod iomemory_vsl`).
5. **Register the disk with Longhorn (manual, deliberate):**
   ```
   # on node N, once /dev/fioa exists and is attached:
   mkfs.ext4 -L iomemory0 /dev/fioa
   mkdir -p /var/lib/harvester/iomemory-disks/fioa
   # add a persistent mount (e.g. /oem fstab) for /dev/fioa -> that path
   ```
   Then add it to `nodes.longhorn.io/N` `.spec.disks` with `tags: [iomemory]`,
   `allowScheduling: true`.
6. **Repeat** for the next node. With 2+ ioMemory disks, `longhorn-iomemory`
   PVCs provision. Bump SC `numberOfReplicas` to 3 once all three are in.

## Using it
- **Shared/RWX-ish workloads (mail maildir):** `storageClassName: longhorn-iomemory`.
- **Databases (GitLab CNPG, HA recorder Postgres):** prefer **local-path PV on the
  ioMemory mount + app-native replication** (CNPG streaming) over Longhorn — lowest
  latency, HA at the app layer. Longhorn fast tier is for non-DB latency-sensitive
  volumes.

## Rollback
`kubectl label node N mdapi.ch/iomemory-` stops the loader pod; `rmmod
iomemory_vsl` unloads (detach Longhorn disk first). Deleting the bundle removes
the SC + ES; it never auto-touched node storage.
