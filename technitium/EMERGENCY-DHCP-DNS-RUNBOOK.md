# Emergency DHCP/DNS on bpi-r4 — full-cluster-outage runbook

When the **whole cluster is down**, Technitium (DHCP+DNS, in-cluster) is gone and
the house loses DHCP + DNS. This is the playbook to keep the house online and to
revert cleanly once the cluster is back. (For primary→secondary DHCP failover
*within* a healthy cluster, see `DHCP-FAILOVER-RUNBOOK.md` instead.)

Origin: 2026-06-06/07 incident (Harvester-upgrade rootfs corruption + a
maintenance-drain cascade → all 3 nodes down for hours).

## 0. Get a shell on bpi-r4 if SSH is refused
bpi-r4's dropbear sometimes dies during network reloads. Restart it via LuCI ubus
(works even when :22 is closed), root pw in Akeyless:
```
TOKEN=$(curl -sk -X POST https://192.168.1.254/ubus -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":"<root-pw>"}]}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"][1]["ubus_rpc_session"])')
curl -sk -X POST https://192.168.1.254/ubus -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"call\",\"params\":[\"$TOKEN\",\"rc\",\"init\",{\"name\":\"dropbear\",\"action\":\"restart\"}]}"
```

## 1. Bring up emergency DHCP/DNS (bpi-r4)
The known-good emergency config is staged on bpi-r4 (survives reboot in /root,
re-applied to the dnsmasq conf-dir as needed):
- `/root/dhcp.good` — production `/etc/config/dhcp` (relay→Technitium .55, forward→Unbound .1)
- `/root/dhcp.emergency-<ts>` — last emergency snapshot (local DHCP server + reservations)
- `/root/revert-emergency-dhcp.sh` — reverts emergency → production
- static busybox at `/www/b` (x86_64) for recovery shells on the nodes

To go to emergency mode:
1. Add VIP `.1` to br-lan so clients' DNS (handed out as 192.168.1.1) still lands:
   `uci add_list network.lan.ipaddr='192.168.1.1/32'; uci commit network; /etc/init.d/network reload`
2. Enable local DHCPv4 + reservations on bpi-r4: restore the emergency snapshot, or
   regenerate reservations from the latest Technitium backup (see §3), drop into the
   **real conf-dir** `/tmp/dnsmasq.cfg<id>.d/` (NOT `/tmp/dnsmasq.d` — that is ignored),
   set `dhcp.lan.dhcpv4=server`, `authoritative=1`.
3. Give dnsmasq real upstreams (Unbound .1 is down): `uci add_list dhcp.@dnsmasq[0].server='45.90.28.16'`
   (NextDNS) + `45.90.30.16` + `9.9.9.9` (Quad9). `noresolv=1` means **upstreams are
   mandatory** — without them dnsmasq REFUSES everything.
4. `/etc/init.d/dnsmasq restart`. Verify: `nslookup google.com 192.168.1.1`.

Reservations honoured: dnsmasq serves dhcp-host entries for the whole /24 as long as
SOME dhcp-range covers the subnet — no need for per-band `static` ranges (those broke
dnsmasq on 2026-06-07; don't re-add them).

## 2. Revert to production (cluster back)
Gate: `kubectl -n split-horizon get endpoints unbound`, `-n technitium get endpoints
technitium-dns technitium-dhcp` all have ready addresses (stable a few min). Then:
```
ssh bpi-r4 'sh /root/revert-emergency-dhcp.sh'
```
It snapshots current, restores `/root/dhcp.good`, removes `.1` from br-lan (frees it for
MetalLB→Unbound), restarts odhcpd+dnsmasq, and falls back to NextDNS if Unbound .1 isn't
answering yet. Verify Technitium is leasing via the relay: check primary pod logs for
`[192.168.1.254:67] DHCP Server leased ...`.

## 3. Where the reservations/zones are backed up (use this, don't do Longhorn forensics)
**Off-site** Garage (salt+pepper), bucket `config-backups`, prefix `technitium/`:
`technitium/<date>/technitium-primary-backup.zip` (full `/api/settings/backup`:
scopes + zones + dns.config). Job: `f/infra_health/technitium_zone_backup`. Also
replicated to B2+SFTP. To regenerate bpi-r4 dnsmasq reservations: unzip → parse
`scopes/lan.scope` (reserved leases: `06 <6-byte MAC> 01 <4-byte IP>` after each
length-prefixed hostname) → `dhcp-host=<mac>,<ip>,<name>`.
> Pre-2026-06-07 this backup pointed at the *in-cluster* bootstrap Garage and was
> useless in a full outage — that's fixed (now off-site).

## 4. Avoid causing this in the first place
- **Before draining/rebooting a node:** scale heavy burst workloads to 0 —
  `kubectl -n frigate scale deploy frigate --replicas=0` — and check headroom with
  `kubectl top nodes`. Frigate does software video decode (no Coral) → ~8 CPU; if it
  reschedules onto a loaded peer during a drain it starves etcd → rke2 restart loop →
  cascade (root of the 2026-06-06 outage). Re-scale after.
- **Never kill etcd/containerd on >1 node** to "break a restart loop" — that tripped the
  watchdog/quorum-loss all-nodes-down. Use `systemctl stop rke2-server`, wait for quorum,
  recover one node at a time.
- Don't stack risky node maintenance on a cluster that's not fully healthy.
