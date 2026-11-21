"""node-exporter must not statfs containerd's own private mounts.

2026-09-24: ~120-185 sandbox shm tmpfs mounts per node (/run/k3s/containerd/...) reported
node_filesystem_device_error=1 (statfs EACCES, not a disk fault) -> excluded in fleet !511.
2026-10-24..11-06 (o2 row infra-37y16): the O2 alert lib_host_filesystem_device_error paged
3 days in 7 on a SECOND path of the same kind: containerd's image-unpack scratch mounts
/var/lib/rancher/rke2/agent/containerd/tmpmounts/containerd-mountNNN (ext4 bind of the root
disk, parent dir 0700). Each lives a few seconds, so one scrape in a thousand lands on one,
and its single EACCES sample pages. 10 of 10 hits in 14 d were this path, on all 4 nodes.
Real mounts on the same disks (etcd db, kubelet root, Longhorn) must stay collected.
"""

from __future__ import annotations

import re
import unittest

import yaml

from tests.fleetlib import load_docs

CONFIG = "rancher-monitoring-config/rancher-monitoring-helmchartconfig.yml"
FLAG = "--collector.filesystem.mount-points-exclude="

# Paths observed in node_filesystem_device_error == 1 (VictoriaMetrics, 14 d to 2026-11-07).
CONTAINERD_PRIVATE = [
    "/var/lib/rancher/rke2/agent/containerd/tmpmounts/containerd-mount3481428965",
    "/var/lib/rancher/rke2/agent/containerd/tmpmounts/containerd-mount1900999484",
    "/run/k3s/containerd/io.containerd.grpc.v1.cri/sandboxes/abc/shm",
]
# Real filesystems whose fill level and errors we still want.
REAL = [
    "/",
    "/var/lib/rancher/rke2/server/db",
    "/var/lib/rancher",
    "/var/lib/rancher/rke2/agent/containerd",
    "/var/lib/harvester/extra-disks/abc",
    "/var/lib/longhorn",
    "/usr/local",
]


def exclude_regex() -> re.Pattern[str]:
    (doc,) = [d for d in load_docs(CONFIG) if d.get("kind") == "HelmChartConfig"]
    values = yaml.safe_load(doc["spec"]["valuesContent"])
    args = values["prometheus-node-exporter"]["extraArgs"]
    (flag,) = [a for a in args if a.startswith(FLAG)]
    return re.compile(flag[len(FLAG):])


class NodeExporterMountExclude(unittest.TestCase):
    def test_containerd_private_mounts_are_not_collected(self):
        rx = exclude_regex()
        self.assertTrue(CONTAINERD_PRIVATE)
        for path in CONTAINERD_PRIVATE:
            self.assertTrue(rx.search(path), f"{path} would be statfs'd (EACCES -> device_error)")

    def test_real_filesystems_are_still_collected(self):
        rx = exclude_regex()
        self.assertTrue(REAL)
        for path in REAL:
            self.assertIsNone(rx.search(path), f"{path} would lose its filesystem metrics")


if __name__ == "__main__":
    unittest.main()
