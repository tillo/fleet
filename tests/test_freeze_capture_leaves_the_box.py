"""A node that freezes must get its last kernel words off the box (infra-m42.10, infra-kd3t).

quo 11-17/11-18 and qui 11-20: the whole box froze, systemd stopped petting the hpwdt watchdog
and iLO hard-reset it ~120 s later. Nothing survived: the journal lives on the disk the freeze
blocks, pstore/ERST is filled only by a panic, and console_loglevel was 1 (EMERG only).

So:
  * netconsole streams kernel messages over UDP to the bpi-r4 listener (192.168.1.254, one
    port per node, files /persist/netconsole/<node>.log on the router). It needs no disk and
    survives a cluster outage. The router MAC is resolved at runtime, the target is built in
    configfs on mgmt-br (netpoll over the mgmt-bo LACP bond was proven on quarto 2026-11-21).
    OPT-IN ONLY (mdapi.ch/netconsole=true): with netpoll on mgmt-br the bridge refuses every new
    veth port, and every VM NIC is a veth on mgmt-br; the Gen8s refuse it outright (ENOTSUPP).
  * console_loglevel 4 lets ERR and above (hung task, soft lockup, I/O errors, oom kills) reach
    the consoles, netconsole included. Measured 2026-11-21: zero ERR-level lines in 7.5 h on any
    node, so this adds no steady traffic.
  * every panic (hung_task_panic, softlockup_panic, oops) prints all-CPU backtraces and the
    D-state tasks (panic_print 0x40|0x80, v6.12 kernel/panic.c) before kmsg_dump writes ERST.

And one thing it must NOT do: arm the hpwdt pretimeout while hpwdt.kdumptimeout is -1 (the
default, read-only, boot-time). hpwdt's NMI handler then calls hpwdt_stop() BEFORE panicking
(v6.12 drivers/watchdog/hpwdt.c hpwdt_pretimeout), so a panic that hangs writing ERST leaves the
node frozen with no hardware reset at all: 2026-09-03, qui and quo sat ~55 min until a hand
reset. hpwdt accepts a runtime pretimeout of 9 s (proven on quarto 2026-11-21, reverted), so the
guard has to live here, not in the driver.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import fleetlib

NETCONSOLE = "harvester-node-config/netconsole.yml"
LOCKUP_PANIC = "harvester-node-config/lockup-panic.yml"
WATCHDOG = "harvester-node-config/watchdog.yml"

ROUTER = "192.168.1.254"
ROUTER_MAC = "f2:9f:f9:e8:db:1c"
# The bpi-r4 listener's port map (/persist/netconsole/listen.sh on the router).
LISTENER_PORTS = {"qua": "6661", "qui": "6662", "quo": "6663", "quarto": "6664"}
NODE_IPS = {"qua": "192.168.1.193", "qui": "192.168.1.190", "quo": "192.168.1.192",
            "quarto": "192.168.1.188"}

KERN_ERR = 3
PANIC_PRINT_ALL_CPU_BT = 0x40
PANIC_PRINT_BLOCKED_TASKS = 0x80
PANIC_PRINT_TASK_INFO = 0x01  # every task's stack: far too long for a 9-120 s panic window


def _cloudinit(rel: str) -> dict:
    (cloudinit,) = fleetlib.load_docs(rel)
    return cloudinit


def _stages(rel: str) -> dict:
    return yaml.safe_load(_cloudinit(rel)["spec"]["contents"])["stages"]


def _conf(text: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"^\s*([\w.]+)\s*=\s*(.+)$", text, re.M)}


def _stage_sysctls(rel: str, stage: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for step in _stages(rel)[stage]:
        for f in step.get("files", []):
            if f["path"].startswith("/etc/sysctl.d/"):
                out.update(_conf(f["content"]))
    return out


def _netconsole_script() -> str:
    (step,) = _stages(NETCONSOLE)["network"]
    (script,) = [c for c in step["commands"] if "modprobe netconsole" in c]
    return script


STUBS = {
    "uname": 'echo "$STUB_HOST"',
    "modprobe": 'echo "$*" >> "$STUB_LOG"',
    "ping": "exit 0",
    "sleep": "exit 0",
    "ip": r'''case "$*" in
  "-4 -o addr show dev mgmt-br") [ -n "$STUB_IP" ] && echo "9: mgmt-br    inet $STUB_IP/24 brd 192.168.1.255 scope global mgmt-br\       valid_lft forever preferred_lft forever" ;;
  "neigh show 192.168.1.254 dev mgmt-br") [ -n "$STUB_MAC" ] && echo "192.168.1.254 lladdr $STUB_MAC REACHABLE" || echo "192.168.1.254 FAILED" ;;
  *) echo "unexpected ip $*" >&2; exit 2 ;;
esac''',
}


def run_netconsole(host: str, ip: str = "", mac: str = ROUTER_MAC,
                   seed: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the CloudInit network-stage script against stub tools and a fake configfs."""
    tmp = Path(tempfile.mkdtemp(prefix="netconsole-test-"))
    bindir = tmp / "bin"
    bindir.mkdir()
    for name, body in STUBS.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    target = tmp / "configfs" / "netconsole" / "bpi-r4"
    if seed:
        target.mkdir(parents=True)
        for k, v in seed.items():
            (target / k).write_text(v + "\n")
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin", "STUB_HOST": host,
           "STUB_IP": ip or NODE_IPS.get(host, "192.168.1.250"), "STUB_MAC": mac,
           "STUB_LOG": str(tmp / "calls.log"), "NETCONSOLE_CONFIGFS": str(tmp / "configfs")}
    proc = subprocess.run(["sh", "-c", _netconsole_script()], env=env,
                          capture_output=True, text=True, timeout=30)
    return proc, target


def _attrs(target: Path) -> dict[str, str]:
    return {p.name: p.read_text().strip() for p in target.iterdir()}


class NetconsoleTarget(unittest.TestCase):
    def test_every_node_streams_to_its_own_listener_port(self):
        for host, port in LISTENER_PORTS.items():
            with self.subTest(host=host):
                proc, target = run_netconsole(host)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(_attrs(target), {
                    "dev_name": "mgmt-br", "local_ip": NODE_IPS[host], "remote_ip": ROUTER,
                    "remote_port": port, "remote_mac": ROUTER_MAC, "enabled": "1"})

    def test_rerun_on_an_enabled_target_rewrites_it(self):
        # The live activation re-runs the stage over the hand-made pilot target.
        proc, target = run_netconsole("quarto", seed={
            "enabled": "1", "dev_name": "mgmt-br", "remote_port": "9999",
            "remote_mac": "00:00:00:00:00:01"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(_attrs(target)["remote_port"], "6664")
        self.assertEqual(_attrs(target)["remote_mac"], ROUTER_MAC)
        self.assertEqual(_attrs(target)["enabled"], "1")

    def test_unknown_host_is_skipped_not_sent_to_another_nodes_file(self):
        proc, target = run_netconsole("newnode")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(target.exists())

    def test_unresolved_router_mac_fails_loudly_instead_of_broadcasting(self):
        proc, target = run_netconsole("qua", mac="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("netconsole", proc.stderr)
        self.assertFalse((target / "enabled").exists())

    def test_runs_in_the_network_stage(self):
        self.assertIn("network", _stages(NETCONSOLE))

    def test_is_opt_in_never_on_every_node(self):
        # netconsole on mgmt-br puts netpoll on the bridge, and from then on br_add_if() calls
        # br_netpoll_enable() for every new port; a veth (IFF_DISABLE_NETPOLL) is refused. Every
        # VM NAD (lan, dmz, wan, pppoe, adlan) is a bridge on mgmt-br, so a node that booted with
        # it could not attach a VM NIC, rancher-mgmt included (2026-11-21). mgmt-bo and the NICs
        # are enslaved, which netpoll_setup() refuses too. So: only a node labelled on purpose.
        selector = _cloudinit(NETCONSOLE)["spec"]["matchSelector"]
        self.assertEqual(selector, {"mdapi.ch/netconsole": "true"})

    def test_never_suggests_rerunning_the_whole_network_stage(self):
        # /oem/90_custom.yaml (Harvester's own network config) runs in the same stage.
        raw = (fleetlib.repo_root() / NETCONSOLE).read_text()
        self.assertNotIn("run-stage network", raw)


class ConsoleLoglevel(unittest.TestCase):
    def test_err_and_above_reach_the_consoles(self):
        printk = _stage_sysctls(NETCONSOLE, "network")["kernel.printk"].split()
        # A message reaches a console when its level is BELOW console_loglevel.
        self.assertGreater(int(printk[0]), KERN_ERR)


class PanicDump(unittest.TestCase):
    def test_soft_lockup_dumps_every_cpu(self):
        self.assertEqual(_stage_sysctls(LOCKUP_PANIC, "boot")["kernel.softlockup_all_cpu_backtrace"], "1")

    def test_panic_prints_cpu_backtraces_and_blocked_tasks_but_not_every_task(self):
        bits = int(_stage_sysctls(LOCKUP_PANIC, "boot")["kernel.panic_print"], 0)
        self.assertTrue(bits & PANIC_PRINT_ALL_CPU_BT)
        self.assertTrue(bits & PANIC_PRINT_BLOCKED_TASKS)
        self.assertFalse(bits & PANIC_PRINT_TASK_INFO)


class WatchdogPretimeout(unittest.TestCase):
    def test_pretimeout_stays_off_while_kdumptimeout_stops_the_watchdog(self):
        (step,) = _stages(WATCHDOG)["boot"]
        conf = "".join(f["content"] for f in step["files"])
        pre = re.search(r"^\s*RuntimeWatchdogPreSec\s*=\s*(\S+)", conf, re.M)
        self.assertTrue(pre is None or pre.group(1) in {"0", "off"},
                        "arming the hpwdt pretimeout needs hpwdt.kdumptimeout>=0 on the kernel "
                        "command line first (see this file's docstring)")


if __name__ == "__main__":
    unittest.main()
