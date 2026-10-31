# harvester-node-config

Node OS settings for the mdapi-prod Harvester nodes, as `CloudInit` CRs
(`node.harvesterhci.io/v1beta1`). The mechanics and the node-manager rules that constrain them
are in [`fleet.yaml`](fleet.yaml). This file is the other half: **what every node is supposed to
carry, and where each file stands.**

Before this bundle, every setting was a file someone copied by hand into `/oem` or
`/etc/rancher/rke2/config.yaml.d` on each node. A node added later (quarto) quietly missed
some of them. A file that failed to take effect also went unnoticed: the 07-28 kubelet memory
reservation sat inert for three months (infra-y6ac).

## Inventory, 2026-10-27

Measured over SSH on all four nodes (sha256 prefix per node; `=` means the same as qua). Roles:
qua, qui and quo are control-plane + etcd (HP DL360p Gen8, ixgbe 2x10G, P420i, fio cards);
quarto is a worker (P408i-a, tg3 4x1GbE, no fio).

### Ported: CRs in this bundle

| File on the node | CR / bundle file | Selector | qua | qui | quo | quarto |
|---|---|---|---|---|---|---|
| `/oem/91-pin-mgmt-br-mac.yaml` | `mgmt-br-mac-pin.yml` | managed | 3c75eca4 | = | = | = |
| `/oem/94_bond_xmit_hash.yaml` | `bond-xmit-hash.yml` | managed | 187c6caa | = | = | 123c10cf ¹ |
| `/oem/94_nic_rxring.yaml` | `nic-rx-ring.yml` | managed | b095bccc | = | = | fdcc37ca ² |
| `/oem/95_watchdog.yaml` | `watchdog.yml` | managed | 86d81b82 | = | = | = |
| `/oem/95b_lockup_panic.yaml` | `lockup-panic.yml` | managed | d3381215 | = | = | = |
| `/oem/99_sysctl_maxmapcount.yaml` | `sysctl-max-map-count.yml` | managed | 20d9a032 | = | = | = |
| `config.yaml.d/45-apiserver-gogc.yaml` | `apiserver-gogc.yml` | control-plane | ed7984d6 | = | = | — ³ |
| `config.yaml.d/99-toleration-defaults.yaml` | `apiserver-toleration-seconds.yml` | control-plane | 24bfaca4 | = | = | — ³ |
| `config.yaml.d/99-rejoin-server.yaml` (`etcd-arg` only) | `etcd-timeouts.yml` | control-plane | 811cd23f | d4dda77e | = | — ³ |
| `config.yaml.d/99-kubelet-reserve.yaml` | `kubelet-memory-reserve.yml` (!1439) | label-staged | 5cfa6316 | = | = | — |

*managed* = `harvesterhci.io/managed: "true"` (every Harvester node); *control-plane* =
`node-role.kubernetes.io/control-plane: "true"`.

1. Same script on all four; only the `name:` text differed.
2. Quarto's generic script (each NIC to its own maximum) replaces the Gen8 one, which set only
   eno1 and left eno2, the second LACP member, at the 512 default. See `nic-rx-ring.yml`.
3. Correct as it is: quarto runs no apiserver and no etcd. The control-plane selector gives a
   promoted node these settings automatically.

The `/oem` files keep their filenames, so each CR takes the hand copy over in place and nothing
is left to delete. The `config.yaml.d` settings move to `99-zz-mdapi-*` drop-ins written at boot.
Each hand copy is retired after that node's first boot under the CRs, using the ROLLOUT block in
its CR file (infra-4u36). Until then, the hand copy and the drop-in set the same values twice,
which is harmless.

Status 2026-10-28: qui is done. Its 2026-10-27 boot proved the drop-ins, so
`45-apiserver-gogc.yaml` and `99-toleration-defaults.yaml` became `.retired-20261028`, and
`99-rejoin-server.yaml` was cut to its `server:` line. The merged rke2 config lost only the duplicated values. qua and quo keep
their hand copies until their own proof boot, which is expected to be the Harvester 1.9.1 hop.
The parity probe's ALLOW list now names only qua and quo for these files.

### Hardware- or node-specific: not ported yet

These need their own filenames and a hardware-class or per-node selector, because a CR that
does not match a node deletes that filename there (see `fleet.yaml`). They are boot-critical on
the fio nodes (etcd, Longhorn, Ceph), so they come after the mechanism has proved itself on the
settings above (infra-yxno).

| File | Where | Why it differs |
|---|---|---|
| `/oem/94_io_scheduler.yaml` | qua/qui/quo 118650ce, quarto 0ee15457 | The Gen8 version forces `rotational=0` on P420i SSD volumes. Quarto's keeps it truthful, because Ceph derives an OSD's device class from it. |
| `/oem/95_fio_udev.yaml` | qua 906e2228, qui/quo c7b0db1d | fio device naming, per card layout |
| `/oem/95b_lh_fio2_mount.yaml` | qua, qui, quo, each different | per-node Longhorn fio disk mounts |
| `/oem/95c_fio_discard.yaml` | quo only | quo's fio card |
| `/oem/96_fio_resize.yaml` | qua/qui/quo 6966a9d2 | fio format; identical but fio-only |
| `/oem/96_iomemory.yaml` | qua 17f9cbd3, qui/quo 421431ec | ioMemory VSL4 driver load. The `.ko` lives node-local in `/usr/local` and must be rebuilt for every kernel. |
| `/oem/96b_iomemory_vsl3.yaml` | qua only | the forked VSL3 driver for qua's ioDrive2 |
| `/oem/97_fio_etcd.yaml` | qua, qui, quo, each different | etcd data directory on fio |
| `/oem/98_rook_ceph.yaml` | qua 4eb9520e, qui/quo 4db89ba6, quarto 073bb2b4 | Rook/Ceph state binds; the Gen8 version also mounts a fio partition |

### Harvester-owned: leave alone

`/oem/90_custom.yaml`, `91_cleanup_kubelet.yaml`, `99_settings.yaml` and
`99_disable_lh_multipathd.yaml` are written by the Harvester installer or `upgrade_node.sh`.
`90_custom.yaml` and `99_settings.yaml` are on node-manager's deny list. Quarto lacking
`99_disable_lh_multipathd.yaml` is not a gap: the next upgrade writes it.
`config.yaml.d/40-disable-charts.yaml`, `50-rancher.yaml`, `90-harvester-*.yaml`,
`91-harvester-cdi.yaml`, `92-harvester-kube-audit-policy.yaml`, `99-max-pods.yaml`,
`99-z00-*` and `99-z01-*` are written by Harvester or Rancher. `40-rancherd.yaml` (qui only) is
left from the 2024 bootstrap.

### Local on purpose

- `99-rejoin-server.yaml`'s `server: https://192.168.1.191:9345` (qui, quo): join plumbing for
  a single node.
- `99-traefik.yaml` (all four, sha256 48785891, `ingress-controller: [ingress-nginx, traefik]`):
  the Harvester 1.9 pin from infra-hfoy, which replaced `99-ingress-controller.yaml` on the
  servers on 2026-10-27. `upgrade_node.sh` tests for this exact path, and
  `pre-reboot-guard.sh` check 6 enforces it. It is a CR candidate later. Never make it the
  scalar: RKE2 1.37 and later exit fatally on it.

### Obsolete: retired 2026-10-28 (infra-4u36)

They were retired before the 1.9 upgrade reboot, because two of them pin 1.8 image content.
Each one is now `<name>.retired-20261028` on its node. Rolling back is a single `mv`, and the
files take effect only at boot.

- `/oem/92_cafix.yaml` (quo): bind-mounted June copies of the p11-kit trust sources over the
  image's copies (harvester#10687 NUL corruption). quo's own image files, read underneath the
  mounts, are byte-identical to those copies, and so are the other three nodes'. The mounts fixed
  nothing, and left in place they would have pinned quo's CA trust against every image update.
  The active mounts go away at quo's next boot. `/usr/local/p11-fix` stays as a recovery source.
- `/oem/99_dbus_fix.yaml` (qua): written for the 1.8.2 `system.conf` corruption. It was proven
  inert: qua booted after it was written, yet nothing is bind-mounted. The image's `system.conf`
  is clean, and the `/usr/share/dbus-1` tree hashes the same on all four nodes.
- `99-kubelet-reserve.yaml` (qua, quo; qui's went with infra-y6ac on 10-27): inert since 07-28,
  because `99-max-pods.yaml` replaces its `kubelet-arg`. It is superseded by
  `kubelet-memory-reserve.yml`.
- `*.bak*`, the older `*.retired-*` and `*.phase2-new` files in `/oem` and `config.yaml.d` were
  clutter rather than configuration, since yip and rke2 read only `*.yaml`/`*.yml`. They were
  removed from the nodes after a full sha256-verified snapshot of `/oem` and `config.yaml.d`
  was taken per node (kept on the jump seat, not in git: `90_custom.yaml` and `50-rancher.yaml`
  hold install secrets).

After the 1.9 upgrade reboot, check that quo's p11-kit trust sources and qua's `system.conf`
are free of NUL bytes. A newly corrupted image is the one case where these fixes would be needed again.

### Not CloudInit at all

The kernel command line lives in `/oem/grubenv` (`third_party_kernel_args`: `multipath=off`,
`ifname=` pins, `psi=1`, `hpwdt.pretimeout=0`, `systemd.debug_shell=ttyS1`), and it matches on
all four nodes. It is outside node-manager's reach. `NodeBootBaselineDrift`
(`monitoring-rules/68-node-boot-baseline.yml`) catches `psi` and the watchdog pretimeout going
missing.

## Adding a setting

1. One CR per setting, one file per CR. Write a header comment covering what it does, why,
   and a ROLLOUT block if it replaces a hand copy.
2. The rke2 drop-ins are `99-zz-mdapi-*.yaml` and use `<key>+:`. A plain list key replaces
   everything before it (memory `rke2_dropin_plain_list_key_replaces_use_plus_2026_10_27`).
3. Adopting an existing `/oem` file means using the same filename *and* a selector that
   matches every node that has it today.
4. Leave `paused` out unless you mean `paused: true`. A `paused: false` never reaches the live
   object, so the bundle stays Modified forever (see `fleet.yaml`).
5. Before merging, check it with `kubectl create --dry-run=server -f <file>`. The admission
   webhook rejects a duplicate filename and contents that are not YAML.
