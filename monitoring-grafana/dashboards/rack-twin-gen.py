#!/usr/bin/env python3
"""
Generate the "mdapi — Rack" Grafana dashboard: a digital twin of the Carnotzet
rack plus the readouts around it.

ARCHITECTURE — ONE canvas, and everything is in it
--------------------------------------------------
The dashboard is a single canvas panel (`panels = [canvas]`): the 42U drawing,
the wall chips beside it and the readout column to its right. The readouts used
to be a row of `stat` panels underneath, resizing with the viewport; they were
folded into the canvas as the right-hand column, which is what gave the rack
its height back (14 -> 17 px per U) and makes 42 units readable from across
the cellar. Nothing outside the canvas exists any more.

That makes the clipping rule bind on the WHOLE board: canvas elements are
absolute CSS pixels and never scale, so a canvas sized for the kiosk is simply
CLIPPED in a narrower browser (no horizontal scrollbar; panZoom does not help).
Vertical overflow is fine in a browser, horizontal is not, so the canvas is
kept 1270px wide and the readout column stops at x=1265.

⛔ A Grafana panel is `38*h - 8` CSS tall (GRID_CELL_HEIGHT 30 + VMARGIN 8), NOT
30*h. At kiosk scale 1.3 the viewport is 1477x923 and leaves ~845 CSS once the
toolbar and padding are off, so the canvas takes the whole budget at h=22 =
828 CSS (the stat-panel era split the same 828 as canvas h=18 + 8 + stats h=4).
Getting this wrong now clips the FOOT of the rack (the frame ends at y=776,
the retired units at U1-5 first) below a fold the wall cannot scroll.

⚠️ bone must run `--force-device-scale-factor=1.3`.

WHY canvas AND NOT 3D: bone is a Pi 3B+ with --disable-gpu and 905 MB RAM.
Every query below was checked against live VictoriaMetrics before generating.

WHERE THIS LIVES, AND WHY IT IS NOT GITOPS
------------------------------------------
This file sits INSIDE the `monitoring-grafana` bundle directory but Fleet never
reads it: `monitoring-grafana/.fleetignore` excludes `dashboards/` wholesale,
which is why `regen-cm.sh` has always been able to live here too. Nothing in
this directory is applied to the cluster — the dashboards reach Grafana only as
data embedded into `../grafana-dashboards-cm.yml`. Do not remove that
.fleetignore entry: without it Fleet would try to apply a 250 KB Grafana JSON as
a Kubernetes manifest.

It was moved here from `~/rack-twin-gen.py` on 2026-09-05 so that generated
output and its source are versioned together. ⭐ CI enforces that: `lint.py`
Check F re-runs this script and fails the pipeline if `Overview/mdapi-rack.json`
does not match its output, and again if `grafana-dashboards-cm.yml` does not
match the JSON on disk. So the two habits that used to rot silently — editing
the JSON by hand, and regenerating the JSON without re-running regen-cm.sh — are
now build failures rather than a dashboard that quietly serves last week.

TO CHANGE THE DASHBOARD
-----------------------
    python3 monitoring-grafana/dashboards/rack-twin-gen.py   # rewrites the JSON
    ./monitoring-grafana/dashboards/regen-cm.sh              # embeds it in the CM
    python3 lint.py                                          # proves both are in sync
"""
import json, sys, os

DS = {"type": "prometheus", "uid": "victoriametrics"}
DROP_TIME = [{"id": "filterFieldsByName", "options": {"exclude": {"names": ["Time"]}}}]

# ------------------------------------------------------------- geometry -----
CANVAS_W, CANVAS_H = 1270, 820
UPX      = 17
TOP_Y    = 56
WALL_X   = 16
CHIP_L   = 32
CHIP_W   = 212
RACK_L   = 285
RACK_W   = 650
DEV_L    = RACK_L + 6
DEV_W    = RACK_W - 12
# Readouts live in a right-hand column INSIDE the canvas now, so the stat
# panels below could go and the rack got its height back (14 -> 17 px per U).
RCOL_L   = 962
RCOL_W   = 303

# right-anchored value columns inside a rack row; nothing ever overlaps
COL_SUB, COL_V1, COL_V2, COL_V3 = DEV_W - 333, DEV_W - 243, DEV_W - 163, DEV_W - 83
## The right edge EVERY rack value shares (x=925 on screen). The last slot's
## width is derived from it rather than typed next to it, so anything else that
## wants to line up with the value column — the monitor's watt readout — anchors
## here and follows if the columns ever move.
COL_END = DEV_W - 4

def u_top(u):     return TOP_Y + (42 - u) * UPX
def u_rect(a, b): return u_top(b), (b - a + 1) * UPX

INK, DIM, FRAME, RETIRED, STATIC = "#e8eaed", "#6b7075", "#15161a", "#25272c", "#31343b"
CHIPBG, WALLC = "#1a1c21", "#2e3138"

I190, I192, I193 = "192.168.1.190:9796", "192.168.1.192:9796", "192.168.1.193:9796"
SW, SALT, PEPPER, MSM = "192.168.1.250", "192.168.1.207", "192.168.1.219", "192.168.1.15"
## Physical edge ports of the C9300 and nothing else — shared by every per-port
## query on the switch so they cannot disagree about what a "port" is (see the
## sw_ports comment for the 24 pseudo-interfaces this keeps out).
PHYS_PORTS = 'ifDescr=~"(GigabitEthernet|TenGigabitEthernet).*"'
ZIGBEE = 'hass_entity_available{entity=~".*_lqi$"}'   # ZHA devices expose an LQI diagnostic

def cpu(i):  return f'100-(avg(rate(node_cpu_seconds_total{{mode="idle",instance="{i}"}}[5m]))*100)'
def mem(i):  return f'100*(1-node_memory_MemAvailable_bytes{{instance="{i}"}}/node_memory_MemTotal_bytes{{instance="{i}"}})'
def scpu(i): return f'100-ssCpuIdle{{instance="{i}"}}'
## ⚠️ salt/pepper "mem" IS NOT the same quantity as qui/quo/qua "mem", even though
## the twin puts them in one column with one `pct` style and one 80/92 band.
## UCD-SNMP `memAvailReal` is MemFree — it EXCLUDES buffers and cache — whereas
## node-exporter's `MemAvailable` (used by mem() above) INCLUDES reclaimable
## cache. So the two x3550s read systematically higher than the DL360s for the
## same real pressure, and a salt row at 80 % is not a quo row at 80 %.
## ⛔ It cannot be corrected here: `memBuffer` and `memCached` are NOT scraped —
## verified 2026-09-05, only memAvailReal/memTotalReal exist for these hosts — so
## the honest form 100*(1-(memAvailReal+memBuffer+memCached)/memTotalReal) returns
## EMPTY today. Add those two OIDs to the snmp exporter's ucd module FIRST, then
## change this line; do not "fix" it by nudging the threshold, which would only
## move the disagreement somewhere less visible.
def smem(i): return f'100*(1-memAvailReal{{instance="{i}"}}/memTotalReal{{instance="{i}"}})'
def link(a): return f'ifOperStatus{{instance="{SW}",ifAlias="{a}"}}'
def hass(m, e): return f'{m}{{entity="{e}"}}'
## salt/pepper have no iLO and no node-exporter, but their IMM answers IPMI:
## f/probe/chassis_health reads sensor "Avg Power" out of the SDR it already
## fetches. (DCMI is refused by IMM1 — see the probe's POWER_SENSORS note.)
## ⭐ Since 2026-09-05 the three DL360s read from HERE too, not from the iLO
## entity in Home Assistant (hass_sensor_power_w{entity="sensor.<n>_ilo_present_power"},
## the old `watt()` helper). Two reasons. qua's iLO entity vanished on
## 2026-09-05 and its watt column went BLANK while chassis_power_watts{host="qua"}
## was fresh at 294 W — the entity was back later the same day, which only
## proves that source comes and goes. And the two sources disagree by 7-10 % on
## the SAME machine (2026-09-05: chassis 310/290/294 vs iLO 286/291/absent),
## while the POWER block below sums chassis_power_watts (rack:power_metered:avg6h),
## so the iLO bars put that disagreement on screen right next to a total they
## could never add up to. One source for all five chassis.
def ipmiw(n): return f'chassis_power_watts{{host="{n}"}}'

## What the UPS costs to run: wall draw MINUS what it delivers to the load.
## ⛔ BOTH SIDES MUST BE TIME-AVERAGED, AND THE WINDOW MUST BE >=6h. The myStrom
## plug and NUT poll on unrelated schedules, so while the rack's draw is trending
## the fast side leads the slow one and the difference measures the skew, not the
## UPS. Convergence measured 2026-09-04 against a 90-day truth of 49.7 W
## (44 669 recorder samples): raw 181.7 · 15m 113.5 · 30m 110.0 · 1h 94.0 ·
## 3h 62.7 · 6h 52.7 · 12h 46.2 · 24h 50.7 · 7d 47.9. Under 6h it reads about
## DOUBLE and looks entirely plausible.
## ⛔ Each side is collapsed with avg() so there is NOTHING to match on. The two
## sides carry different `entity` labels, so a bare subtraction matches nothing
## and renders EMPTY — which reads as "no data yet" rather than as a mistake.
## Until 2026-09-05 that was handled with `- on()`, which was right in principle
## and defeated by the exporter: hass_* series also carry `pod` and `instance`,
## and Home Assistant ran as 6 distinct pods in the 7 days to 2026-09-05 (one
## restart every ~34 h). Whenever a restart falls inside the 6h window each side
## returns MORE THAN ONE series, `on()` demands 1-to-1, and VictoriaMetrics
## answers HTTP 422 ("cannot evaluate ... - on() ...", proven live on a 24h
## window) — the panel goes blank, the silent-empty trap again. Against a ~34 h
## cadence a 6h window hit that ~18 % of the time. avg() over the pod-split
## pieces yields one label-less series per side and is immune to pod churn; it
## is not time-weighted, so across a restart the shorter piece counts as much as
## the longer one — a small lean on a signal this slow, and not a blank.
## While the battery floats (99 %, it never reaches 100) this IS the UPS's own
## consumption. After a real outage the same figure rises and the excess over the
## ~50 W baseline is the charging power — NUT exposes no battery voltage or
## current on this R/T3000, so that subtraction is the only way to see charging.
UPS_SELF_USE = ('avg(avg_over_time(hass_sensor_power_w{entity="sensor.rack_power"}[6h]))'
                ' - avg(avg_over_time(hass_sensor_power_w{entity="sensor.ups_real_power"}[6h]))')

# ------------------------------------------------------------- rack map -----
# (from_u, to_u, kind, name, sub, [(field, expr, style), ...])  — up to 4 values;
# a fourth takes the caption slot, so `sub` is dead text on a four-value row
RACK = [
    ## Battery, then runtime / self-use / load right beside it (user, 2026-09-04).
    ## The UPS's OUTPUT watts are deliberately NOT here: the wall chip already
    ## carries the rack's draw, and output only differed from it by the self-use
    ## figure below, so showing both spent a column to say the same thing twice.
    ## Four values, so this row uses the `sub` caption slot as a value column.
    (41, 42, "live", "UPS  HP R/T3000", "", [
        ("ups_batt", hass("hass_sensor_battery_percent", "sensor.ups_battery_charge"), "batt"),
        ("ups_rt", hass("hass_sensor_duration_s", "sensor.ups_battery_runtime") + "/60", "mins"),
        ("ups_self", UPS_SELF_USE, "wattu"),
        ("ups_load", hass("hass_sensor_unit_percent", "sensor.ups_load"), "load")]),
    (39, 39, "static", "patch panel", "prise 1-4", []),
    ## Four values, so no caption (the old "uplink / PoE" one was already a lie
    ## next to three numbers). Note the last two watt figures side by side: the
    ## PoE total is MEASURED and the self figure is a datasheet ESTIMATE, and the
    ## estimate colour is what tells them apart — that is what `watte` is for.
    (35, 35, "live", "XikeStor  PoE", "", [
        ("xikestor_link", link("xikestor"), "link"),
        # per-AP power, scraped from its web CGI because it answers no SNMP
        ("xikestor_w", 'xikestor_poe_total_watts', "wattp"),
        # UP ports, not PoE ports (user): six links, only four drawing power —
        # the first-floor AP is fed from the mid-house switch, and the sixth is
        # the 10G uplink, counted deliberately. The uplink is the one whose loss
        # isolates the entire AP leaf while every other port still reads healthy,
        # so leaving it out of "ports up" would hide the failure that matters.
        ("xikestor_ports", 'xikestor_ports_linked', "xports"),
        # ⭐ The switch's OWN draw, 12 W modelled. It was inside
        # rack:power_estimate_total:watts (301 W) but rendered nowhere: the
        # visible estimates summed to 289 W (160+45+40+22+11+7+4, 2026-09-05),
        # so the board showed 12 W less than its own total. Added as the fourth
        # value, which consumes the caption slot — see the row comment above.
        ("xikestor_self", 'rack:power_estimate:watts{device="xikestor-self"}', "watte")]),
    (34, 34, "retired", "BOMBEROS   HP 2920", "", []),
    # U24-31 is the monitor; drawn separately so the void inside it can be used.
    # It used to sit at U25-32 on top of mbptillo's shelf at U24. mbptillo was
    # retired for good (2026-09-01) and is off the rack, so the screen now rests
    # DIRECTLY on fntillo's chassis — one U lower, same 8U height.
    (22, 23, "retired", "FNTILLO   DL320s  12x146GB", "", []),
    ## W from IPMI via the chassis probe, same as salt/pepper — see ipmiw().
    (20, 20, "live", "QUI   DL360p Gen8", "cpu/mem/W", [
        ("qui_cpu", cpu(I190), "pct"), ("qui_mem", mem(I190), "pct"), ("qui_w", ipmiw("qui"), "wattsm")]),
    (18, 18, "live", "QUO   DL360p Gen8", "cpu/mem/W", [
        ("quo_cpu", cpu(I192), "pct"), ("quo_mem", mem(I192), "pct"), ("quo_w", ipmiw("quo"), "wattsm")]),
    (16, 16, "live", "QUA   DL360p Gen8", "cpu/mem/W", [
        ("qua_cpu", cpu(I193), "pct"), ("qua_mem", mem(I193), "pct"), ("qua_w", ipmiw("qua"), "wattsm")]),
    (14, 14, "live", "SALT  x3550 M2", "cpu/mem/W", [
        ("salt_cpu", scpu(SALT), "pct"), ("salt_mem", smem(SALT), "pct"),
        ("salt_w", ipmiw("salt"), "wattsm")]),
    (12, 12, "live", "PEPPER  x3550 M2", "cpu/mem/W", [
        ("pepper_cpu", scpu(PEPPER), "pct"), ("pepper_mem", smem(PEPPER), "pct"),
        ("pepper_w", ipmiw("pepper"), "wattsm")]),
    (10, 11, "live", "Cisco C9300  #1", "up/load/err", [
        # ⛔ NOT count(ifOperStatus==1): that counted 53 by including six
        # Port-channels, eight "unrouted VLAN" pseudo-interfaces, both
        # StackPorts, four StackSubs, the two internal AppGigabitEthernet ports,
        # a Vlan SVI and Null0. A bond and its members are the same copper, so
        # counting both double-counts it. Physical edge ports only: 29 up, of 31
        # that carry a description (salt-2 and salt-3 are cabled but down —
        # Cisco suspends a slow LACP member, salt's 100 Mb eno2).
        ("sw_ports", f'count(ifOperStatus{{instance="{SW}",{PHYS_PORTS}}}==1)', "ports"),
        # ⭐ now a PERCENTAGE: ifHighSpeed was added to the ifmib SNMP module
        # 2026-09-01, so the octet counters finally have a denominator.
        # ⛔ SAME physical-port filter as sw_ports, on all three selectors. Until
        # 2026-09-05 the max() ran over EVERY interface and was won by Vlan1 —
        # an SVI, the very pseudo-interface the row above excludes — at 17.9 %,
        # while the busiest real port (Te1/1/8, the bpi-r4 uplink) was 1.3 %:
        # the "load" shown was ~14x the true busiest port and contradicted the
        # ports count beside it. ⚠️ Remaining caveat: in+out are SUMMED against
        # a single direction's ifHighSpeed, so a saturated full-duplex link can
        # legitimately read above 100 %.
        ("sw_util", f'max(((rate(ifHCInOctets{{instance="{SW}",{PHYS_PORTS}}}[5m])'
                    f'+rate(ifHCOutOctets{{instance="{SW}",{PHYS_PORTS}}}[5m]))*8/1e6)'
                    f'/ (ifHighSpeed{{instance="{SW}",{PHYS_PORTS}}} > 0) * 100)', "util"),
        # bad packets across every port, in and out — already walked, never shown
        ("sw_err", f'sum(rate(ifInErrors{{instance="{SW}"}}[15m]))'
                   f'+sum(rate(ifOutErrors{{instance="{SW}"}}[15m]))', "errs")]),
    ## ⛔ The watt figure here is the ESTIMATE FOR THE PAIR, not for this unit.
    ## It is on #2 and not on #1 deliberately: #1 already carries three values,
    ## and a fourth takes the caption slot — which is exactly the caption that
    ## has to say "stack". A 160 W figure sitting on one bar with no caption,
    ## while the other bar shows nothing, reads as "#1 draws it all and #2 draws
    ## zero", which is worse than not showing it. ⛔ Do NOT "fix" this by halving
    ## it onto both bars: 80 W each would be an invention: two units in a
    ## StackPower pair do not split their draw evenly, and nothing here measures
    ## the split. The stack has NO wattage over SNMP at all (IOS-XE 16.12.02
    ## exposes no type-6 sensor), so this is datasheet arithmetic until a metered
    ## PDU exists — which is what the model error in the POWER section is for.
    ( 9,  9, "live", "Cisco C9300  #2", "uptime · stack W", [
        ("sw_uptime", f'sysUpTime{{instance="{SW}"}}/8640000', "days"),
        ("sw_w_est", 'rack:power_estimate:watts{device="c9300-stack"}', "watte")]),
    ( 3,  5, "retired", "SWTILLO   3x HP 2920", "", []),
    ( 2,  2, "retired", "VORTEX   DL360 G5", "", []),
    ( 1,  1, "retired", "EXION   Dell PowerEdge 1950", "", []),
]

# ⭐ Nothing here is rack-mounted and the drawing must say so on its own: a rack
# unit is a full-width bar flush in the frame on a U row, so wall items get a
# narrow chip held off a drawn wall line, at its real position, body unfilled.
# `sub` rows are readouts OF the chip above, not separate devices.
WALL = [
    (128, "bpir4",  "bpi-r4", "wan_carrier", 'bpi_r4_wan_carrier', "link", False),
    (156, "pppoe",  "PPPoE up",  "wan_uptime", 'bpi_r4_pppoe_uptime_seconds/86400', "days", True),
    (180, "sfp",    "SFP temp",  "sfp_temp",   'bpi_r4_sfp_temp_c', "temp", True),
    (204, "onu",    "ONU temp",  "onu_temp",   'bpi_r4_onu_temperature_c', "temp", True),
    # GPS readouts belong ON the adapter that carries the signal, not in a
    # separate panel: the cable from here runs to the receiver on the patio.
    (260, "gps",    "GPS serial adapter", "gps_strat",
     'ntp_probe_stratum{server="192.168.1.58"}', "stratum", False),
    (288, "gpssat", "satellites", "gps_sats", 'gps_satellites_used', "sats", True),
    (312, "gpstdop", "TDOP",      "gps_tdop", 'gps_tdop', "dop", True),
    (336, "gpsoff", "offset us",  "gps_offset",
     'abs(ntp_probe_offset_seconds{server="192.168.1.58"})*1e6', "us", True),
    ## ⚠️ INSTANTANEOUS, and the title says so. Every row in the POWER section
    ## is a 6h average and this chip sits on the same screen: measured
    ## 2026-09-05, plug 1623 W (now) beside "UPS delivers" 1511 W (6h avg) —
    ## subtract them, as the layout invites, and you get 112 W of apparent UPS
    ## self-use while the twin's own self-use row says 40 W. The rule that an
    ## instantaneous and an averaged reading must never share a subtraction is
    ## enforced INSIDE the POWER section and was broken ACROSS the board. Kept
    ## live, because a live wall reading is what a plug is for — labelled so
    ## nobody nets it against the averaged rows.
    (392, "plug",   "rack plug · now", "rack_power",
     hass("hass_sensor_power_w", "sensor.rack_power"), "watt", False),
]

## Third element is the `device` label in rack:power_estimate:watts. ⛔ NOTHING
## on this shelf is metered — not one of these five is on a wattmeter, and the
## C9300 port list cannot help because power is not data. So every watt figure
## here is a datasheet estimate and is rendered in the estimate colour. Their
## only check is collective: they are inside the residual that
## monitoring-rules/47-rack-power-model.yml computes, and the model error says
## whether the set of them adds up.
BEHIND = [("bone", 'probe_success{probe="rack-kiosk"}', "bone"),
          ("firewalla", link("firewalla"), "firewalla"),
          ("aptc", link("aptc"), "aptc"),
          ("nastillo", link("nastillo"), "nastillo"),
          ("santillo", link("santillo"), "santillo")]

elements, targets, overrides = [], [], []

def target(field, expr, into=None):
    (into if into is not None else targets).append(
        {"refId": field, "datasource": DS, "expr": expr, "instant": True,
         "range": False, "editorMode": "code", "legendFormat": field})

def override(field, props, into=None):
    (into if into is not None else overrides).append(
        {"matcher": {"id": "byFrameRefID", "options": field},
         "properties": [{"id": "displayName", "value": field}] + props})

def rect(name, left, top, width, height, *, text=None, field=None, bg=None,
         bgfield=None, size=11, color=INK, align="left", border=None, bw=0,
         colorfield=None):
    """⛔ Bind bgfield or colorfield, or every threshold is silently discarded:
    binding a field to TEXT does not bind its COLOUR."""
    elements.append({
        "type": "rectangle", "name": name,
        "config": {"align": align, "valign": "middle", "size": size,
                   "color": ({"field": colorfield} if colorfield else {"fixed": color}),
                   "text": ({"mode": "field", "field": field} if field
                            else {"mode": "fixed", "fixed": text or ""})},
        "background": {"color": ({"field": bgfield} if bgfield else {"fixed": bg or "transparent"})},
        "border": {"color": {"fixed": border or "transparent"}, "width": bw},
        "constraint": {"horizontal": "left", "vertical": "top"},
        "placement": {"top": top, "left": left, "width": width, "height": height, "rotation": 0}})

def th(steps): return {"id": "thresholds", "value": {"mode": "absolute", "steps": steps}}
def st(v, c):  return {"value": v, "color": c}
def unit(u):   return {"id": "unit", "value": u}
def dec(n):    return {"id": "decimals", "value": n}

## ⛔ 0 AND 2 both mean DOWN here, on purpose. IF-MIB ifOperStatus is NEVER 0:
## 1=up 2=down 3=testing 4=unknown 5=dormant 6=notPresent 7=lowerLayerDown —
## measured on the C9300 2026-09-05: 53 interfaces at 1, 53 at 2, none above 2.
## Until 2026-09-05 this map knew only "0" and "1", so a dropped link fell
## through to FLAT and rendered as a neutral grey "2"; and `xikestor_link` is
## the bgfield of the whole XikeStor bar, so a dead 10G uplink turned that bar
## GREY, not red. "0" stays because two genuine booleans share this style —
## probe_success{probe="rack-kiosk"} (bone) and bpi_r4_wan_carrier — and they
## really do say 0 for down. 6/7 are failures in everything but name (no module
## fitted / the layer underneath is dead) and are red like 2; 3/4/5 are "not
## healthy, not plainly down" and get orange so they never pass for UP.
MAP_LINK = {"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 0},
    "1": {"text": "UP", "color": "green", "index": 1},
    "2": {"text": "DOWN", "color": "red", "index": 2},
    "3": {"text": "testing", "color": "orange", "index": 3},
    "4": {"text": "unknown", "color": "orange", "index": 4},
    "5": {"text": "dormant", "color": "orange", "index": 5},
    "6": {"text": "absent", "color": "red", "index": 6},
    "7": {"text": "lower DOWN", "color": "red", "index": 7}}}]}
MAP_DOOR = {"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "closed", "color": "green", "index": 0},
    "1": {"text": "OPEN", "color": "orange", "index": 1}}}]}
MAP_ALARM = {"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "clear", "color": "green", "index": 0},
    "1": {"text": "SMOKE", "color": "red", "index": 1}}}]}
FLAT = th([st(None, "text")])

STYLE = {
    "pct":   [th([st(None, "green"), st(80, "yellow"), st(92, "red")]), unit("percent"), dec(0)],
    ## Physical-port utilisation (see sw_util). Red at 100 is not a tuned
    ## number: it is where the in+out SUM reaches one direction's line rate,
    ## the point past which the figure stops meaning "busy". No measured
    ## saturation value exists to place it anywhere else.
    "util":  [th([st(None, "green"), st(60, "yellow"), st(85, "orange"), st(100, "red")]),
              unit("percent"), dec(0)],
    "batt":  [th([st(None, "red"), st(40, "orange"), st(80, "green")]), unit("percent"), dec(0)],
    ## UPS load. Same steps as `pct` but its OWN suffix: the four-value UPS row
    ## has no caption, and battery and load were two bare percentages side by
    ## side ("99 % · 14 min · 40 W · 52 %") with nothing to say which was which.
    "load":  [th([st(None, "green"), st(80, "yellow"), st(92, "red")]), unit("suffix: % load"), dec(0)],
    ## OPTICS — SFP and ONU. Not the room: see `rtemp`.
    "temp":  [th([st(None, "green"), st(55, "yellow"), st(68, "orange"), st(78, "red")]),
              unit("celsius"), dec(0)],
    ## Room air. Until 2026-09-05 the room shared `temp`, whose 55/68/78 steps
    ## are right for an SFP and meaningless for air: the server room ran
    ## 28.1–29.5 °C over the 24h to 2026-09-05 and rendered green — and would
    ## still have rendered green at 50 °C. This band keeps today's 28–29.5 green
    ## with headroom and starts talking at 32.
    "rtemp": [th([st(None, "green"), st(32, "yellow"), st(36, "orange"), st(40, "red")]),
              unit("celsius"), dec(0)],
    ## Exhaust fan, where LOW is the failure. It used `pct` until 2026-09-05,
    ## which is inverted for a fan: 0 % — stopped, the one fault that matters —
    ## rendered GREEN and 95 % rendered red. High fan speed is the cooling
    ## SUCCEEDING, not a fault. Measured 50–75 % over the 24h to 2026-09-05, so
    ## green today; orange 10–30, red below 10.
    "fan":   [th([st(None, "red"), st(10, "orange"), st(30, "green")]), unit("percent"), dec(0)],
    "link":  [MAP_LINK, FLAT],
    "days":  [th([st(None, "green")]), unit("suffix: days"), dec(1)],
    ## The C9300's count. Deliberately generic (only total zero is red): its
    ## healthy count legitimately VARIES — salt-2/salt-3 are cabled but
    ## intentionally down (see sw_ports) — so a floor pinned at 29 would flap.
    "ports": [th([st(None, "red"), st(1, "green")]), unit("suffix: ports"), dec(0)],
    ## The XikeStor's count, where SIX is the whole complement (live 2026-09-05)
    ## and anything less is a fault. Under the generic style five of six could
    ## drop — the 10G uplink that isolates the AP leaf included — and it still
    ## read green.
    "xports": [th([st(None, "red"), st(5, "orange"), st(6, "green")]), unit("suffix: ports"), dec(0)],
    ## The wall plug: the rack's whole draw INCLUDING the UPS's own ~40-50 W.
    ## dec(0) — it was the only watt readout with decimals, "1623.30 W" on a
    ## kiosk read from across a cellar. Steps are the rack-total `wattb` ones
    ## (1800/2400) plus that self-use offset, so the plug and "UPS delivers"
    ## change colour on the SAME load; they were 2000/2600, matching nothing.
    "watt":  [th([st(None, "green"), st(1850, "yellow"), st(2450, "orange")]), unit("watt"), dec(0)],
    ## Chassis draw (140–310 W measured). Until 2026-09-05 this also served total
    ## PoE (~32 W), per-port PoE (5–11 W) and UPS self-use (~40 W) — two orders
    ## of magnitude under one 400 W step, so for everything but the chassis the
    ## colour carried nothing.
    "wattsm": [th([st(None, "green"), st(400, "yellow")]), unit("watt"), dec(0)],
    ## UPS self-use (UPS_SELF_USE, ~50 W: 90-day truth 49.7, the 6h window reads
    ## ~52.7). FLAT on purpose. It wore `wattsm` until 2026-09-05, whose first
    ## step is 400 W — eight times anything this figure reaches — so the colour
    ## could never change. But it gets no step of its own either, because the
    ## number has no failure threshold: the only way it moves is UP, and a raised
    ## figure means the battery is CHARGING after an outage, which is the UPS
    ## doing its job — a high-side step would paint healthy behaviour as a fault.
    ## No measured charging figure exists to bound it with (NUT exposes no
    ## battery current on this R/T3000), and a low-side step would flag the two
    ## source entities drifting apart, not the UPS. "text", not green: on this
    ## board green means "measured and FINE", and this reading has no "fine".
    "wattu": [th([st(None, "text")]), unit("watt"), dec(0)],
    ## PoE, per port and total, where LOW is the signal: a port at 0 W is an
    ## access point that is NOT POWERED, so red below 1 W, green from 1 W.
    ## ⛔ No high-side step on purpose — the switch does not expose its PoE
    ## budget over its interface, so any ceiling here would be a guess.
    "wattp": [th([st(None, "red"), st(1, "green")]), unit("watt"), dec(0)],
    ## ⛔ ESTIMATED watts — a datasheet figure, NOT telemetry. Deliberately its
    ## own colour and never green: on this board green means "measured and fine",
    ## and an estimate has not been measured at all. Same principle as rendering
    ## "not monitored" distinctly instead of a reassuring grey — the fact that a
    ## number is modelled is itself information. Flat, because a threshold on a
    ## constant would only ever tell you what you typed.
    "watte": [th([st(None, "#8ab4f8")]), unit("watt"), dec(0)],
    ## The residual: UPS output minus everything metered. Measured, not modelled,
    ## so it keeps a normal scale — but it is a REMAINDER, so it grows whenever a
    ## metered input goes missing as well as when real load is added.
    "wattr": [th([st(None, "green"), st(450, "yellow"), st(600, "orange")]), unit("watt"), dec(0)],
    ## Model error = residual − estimates. The only number here that can be
    ## WRONG, so it is the only one with two-sided thresholds.
    ## ⚠️ The green band is ±75 W and is centred on ZERO while the model is known
    ## to sit at about +60: as of 2026-09-05 the residual is 361 W against 301 W
    ## of datasheet estimates. The band is wide enough that this known gap does
    ## not paint the row a permanent warning colour — a readout that is always
    ## yellow teaches people to stop reading it — but it is deliberately NOT
    ## re-centred on +60, because that would hide the gap instead of tolerating
    ## it. Narrow the band once the missing ~60 W is identified.
    "werr":  [th([st(None, "orange"), st(-150, "yellow"), st(-75, "green"),
                  st(75, "yellow"), st(150, "orange")]), unit("watt"), dec(0)],
    ## How many chassis the metered sum is actually counting. Five is the whole
    ## truth; anything less silently inflates the residual.
    "hosts": [th([st(None, "red"), st(5, "green")]), unit("suffix: /5 metered"), dec(0)],
    ## Age of the last chassis_health push, in SECONDS. ⛔ chassis_power_watts
    ## arrives via pushgateway, which RETAINS a series after its pusher stops, so
    ## the host count above can NEVER notice a dead probe: it stays 5 and green
    ## forever. timestamp() is no help either — it returns the pushgateway's own
    ## scrape time, so every series always looks ~3 s fresh. The real signal is
    ## push_time_seconds{job="chassis_health"} (exactly one series; 73 s old
    ## when measured 2026-09-05). The probe pushes every 15 min, so: yellow at
    ## one missed cycle (1200 s), orange at 2400, red at an hour. The host COUNT
    ## catches "probe runs but one host fails IPMI"; THIS row catches "probe
    ## stopped".
    "age":   [th([st(None, "green"), st(1200, "yellow"), st(2400, "orange"), st(3600, "red")]),
              unit("s"), dec(0)],
    ## Rack-scale totals (~1.1-1.5 kW), so the steps are nothing like the
    ## per-device "wattsm" ones — reusing that style here would paint every
    ## healthy total yellow.
    "wattb": [th([st(None, "green"), st(1800, "yellow"), st(2400, "orange")]),
              unit("watt"), dec(0)],
    "hum":   [th([st(None, "green"), st(70, "yellow")]), unit("percent"), dec(0)],
    "door":  [MAP_DOOR, FLAT],
    "alarm": [MAP_ALARM, FLAT],
    "us":    [th([st(None, "green"), st(1000, "yellow"), st(10000, "red")]), unit("suffix: us"), dec(0)],
    "stratum": [th([st(None, "green"), st(2, "yellow"), st(4, "red")]), unit("suffix: stratum"), dec(0)],
    # A GPS timing fix wants plenty of satellites; under ~5 the geometry suffers.
    "sats":  [th([st(None, "red"), st(5, "orange"), st(8, "green")]), unit("suffix: sats"), dec(0)],
    # DOP is better when SMALLER, so the steps run the other way.
    "dop":   [th([st(None, "green"), st(2, "yellow"), st(5, "orange")]), unit("none"), dec(2)],
    "zigup": [th([st(None, "red"), st(1, "green")]), unit("suffix: up"), dec(0)],
    # any offline device at all takes it off green — the ask, literally
    "zigoff": [th([st(None, "green"), st(1, "red")]), unit("suffix: off"), dec(0)],
    # any sustained error rate on a LAN switch is worth a colour; 0 is normal.
    # ⛔ dec(2), not dec(0): the steps sit at 0.01 and 1, so at dec(0) a rate of
    # 0.4 err/s rendered "0 err/s" painted ORANGE — a number that reads as zero
    # while the colour says problem. Latent, not live: the 24h max was 0 when
    # this was fixed (2026-09-05).
    "errs":  [th([st(None, "green"), st(0.01, "orange"), st(1, "red")]),
              unit("suffix: err/s"), dec(2)],
    "clients": [th([st(None, "text")]), unit("suffix: clients"), dec(0)],
    # UPS autonomy in MINUTES. The 12-minute envelope is the number the whole
    # shutdown choreography is built around, so it gets its own style and is
    # never borrowed from "days".
    # ⛔ Green at 12 — the envelope itself — not 15. Measured runtime over the
    # 24h to 2026-09-05 was 13.1–16.4 min (14.3 at the time), so with green at
    # 15 the row sat ORANGE through a normal day: a readout that is always
    # yellow teaches people to stop reading it. Orange 8–12 is "inside the
    # envelope is gone", red below 8.
    "mins":  [th([st(None, "red"), st(8, "orange"), st(12, "green")]),
              unit("suffix: min"), dec(0)],
}

# ============================================================ 1. the frame ==
rect("title", RACK_L, 2, RACK_W, 20, text="CARNOTZET  RACK  ·  42U", size=15,
     color=INK, align="center")

## Objects RESTING ON the rack, straddling its top edge — not in a U either.
target("smoke_top", hass("hass_binary_sensor_state", "binary_sensor.smoke_sensor_smoke"))
override("smoke_top", STYLE["alarm"])
rect("acc_smoke", RACK_L + 30, 26, 4, 20, bgfield="smoke_top")
rect("chip_smoke", RACK_L + 34, 26, 300, 20, text="  smoke sensor", size=10, bg=CHIPBG)
rect("v_smoke", RACK_L + 212, 26, 118, 20, field="smoke_top", size=11,
     colorfield="smoke_top", align="right")
## The white box on top is the MSM430 ACCESS POINT, not a switch.
target("msm_uptime", f'sysUpTime{{instance="{MSM}"}}/8640000')
override("msm_uptime", STYLE["days"])
rect("acc_msm", RACK_L + 350, 26, 4, 20, bgfield="msm_uptime")
rect("chip_msm", RACK_L + 354, 26, 330, 20, text="  MSM430 access point", size=10, bg=CHIPBG)
rect("v_msm", RACK_L + 552, 26, 128, 20, field="msm_uptime", size=11,
     colorfield="msm_uptime", align="right")
## Lives in the 41 px gutter between the wall column (ends x=244) and the rack
## frame (x=285), right-aligned on the U numbers' edge. It was 42 wide from
## x=239 until 2026-09-05 and overlapped `wall_hdr` by 5 px — invisible only
## because "BESIDE THE RACK" is short and left-aligned; a longer header would
## have collided. 34 px holds "on top" at size 9 with room to spare.
rect("ontop", RACK_L - 38, 26, 34, 20, text="on top", size=9, color=DIM, align="right")

rect("rackframe", RACK_L, TOP_Y - 6, RACK_W, 42 * UPX + 12, bg=FRAME, border="#3a3d44", bw=2)
for u in range(5, 43, 5):
    rect(f"u{u}", RACK_L - 30, u_top(u), 26, UPX, text=str(u), size=9, color=DIM, align="right")

# ============================================================ 2. rack units =
for a, b, kind, name, sub, vals in RACK:
    top, h = u_rect(a, b)
    if kind == "live":
        head = vals[0][0]
        # ⚠️ The FIRST value is also the bar's fill (bgfield=head), so a scrape
        # gap does not redden the row — it UN-PAINTS it: a dead server reads as
        # a blank slot, not a red one. ⛔ Do NOT "fix" this with `or vector(0)`
        # on cpu/mem/watt: a guarded CPU renders a confident green "0 %" for a
        # host that is OFF, which is strictly worse than blank — blank at least
        # looks wrong. The right fix is a distinct "stale/absent" treatment (a
        # colour that means "no reading", the way watte means "modelled"), and
        # nobody has built it yet. Recorded 2026-09-05.
        for f, e, sty in vals:
            target(f, e); override(f, STYLE[sty])
        rect(f"dev_{head}", DEV_L, top, DEV_W, h, text="  " + name, size=11,
             bgfield=head, color="#0b0c0e", border="#00000055", bw=1)
        # Three values leave room for the caption; a FOURTH takes the caption's
        # slot, because at four numbers the caption is the least useful thing in
        # the row. Values always read left to right in the order given.
        if len(vals) > 3:
            slots = ((COL_SUB, 84), (COL_V1, 76), (COL_V2, 76), (COL_V3, COL_END - COL_V3))
        else:
            rect(f"sub_{head}", DEV_L + COL_SUB, top, 84, h, text=sub, size=9,
                 color="#0b0c0e99", align="right")
            slots = ((COL_V1, 76), (COL_V2, 76), (COL_V3, COL_END - COL_V3))
        # ⭐ FLUSH RIGHT: a row with fewer values takes the LAST slots, so its
        # final figure always ends on COL_END. Until 2026-09-05 slots filled
        # left to right and the C9300 #2 row (uptime, stack W) stopped at x=842
        # while every other row ended at 925 — 83 px short, two numbers floating
        # left of the column the whole rack lines up on. ⛔ This is the OPPOSITE
        # of the rule in the ACCESS POINTS section, and deliberately so: there
        # the three columns MEAN the same thing on every row (clients, uptime,
        # W), so a gap must stay in its own column. Rack rows share no column
        # meaning — the third slot is watts on the servers, error rate on
        # C9300 #1, stack watts on #2 — so a ragged right edge is the only thing
        # the eye can pick up, and flush-right is what reads on a wall. It also
        # lands #2's stack-watt figure in the column the servers' watts use.
        # The four-value UPS row fills every slot either way and is untouched.
        for idx, (col, w) in enumerate(slots[len(slots) - len(vals):]):
            # value 0 already tints the fill; show values from index 0 anyway
            # so a single-value row still reads.
            rect(f"v{idx}_{head}", DEV_L + col, top, w, h, field=vals[idx][0],
                 size=11, color="#0b0c0e", align="right")
    else:
        rect(f"dev_{a}_{b}", DEV_L, top, DEV_W, h,
             text="  " + name + ("   · retired" if kind == "retired" else ""),
             size=11, bg=(RETIRED if kind == "retired" else STATIC),
             color=(DIM if kind == "retired" else INK), border="#00000055", bw=1)
        ## ⛔ Until 2026-09-05 `sub` was rendered only on the live branch, so the
        ## patch panel's "prise 1-4" sat in the map looking maintained and
        ## reached no pixel — a string describing real cabling, silently
        ## dropped. Same COL_SUB column as the live rows; DIM rather than the
        ## live rows' dark tint, because the ink here is on a grey bar, not on a
        ## coloured fill. Retired rows carry an empty `sub`, so they emit
        ## nothing and are unchanged.
        if sub:
            rect(f"sub_{a}_{b}", DEV_L + COL_SUB, top, 84, h, text=sub, size=9,
                 color=DIM, align="right")

# ------ the monitor, and the machines stacked in the void behind it ---------
mon_top, mon_h = u_rect(24, 31)
rect("dev_monitor", DEV_L, mon_top, DEV_W, mon_h, bg=STATIC, border="#00000055", bw=1)
rect("mon_label", DEV_L + 8, mon_top + 3, 400, 15,
     text="HP LA2405wg  ·  this screen", size=10, color=INK)
## The screen's own draw, on the screen — also an estimate; a monitor reports
## nothing and is on no meter.
target("screen_w", 'rack:power_estimate:watts{device="screen-la2405wg"}')
override("screen_w", STYLE["watte"])
## Right edge on COL_END, like every value in the rack rows above and below it.
## It was `DEV_W - 92` + 84 wide = x 837–921 until 2026-09-05, 4 px short of
## the 925 the rack column ends on — the one right-aligned watt figure on the
## rack face that did not sit on the line. Same width, only the anchor moved.
rect("mon_w", DEV_L + COL_END - 84, mon_top + 3, 84, 15, field="screen_w", size=10,
     colorfield="screen_w", align="right")
rect("behind_hdr", DEV_L + 8, mon_top + 22, 400, 14,
     text="behind it, on the shelf — not racked · W estimated:", size=9, color=DIM)
for i, (nm, expr, est) in enumerate(BEHIND):
    f = f"{nm}_link"
    w = f"{nm}_w"
    target(f, expr); override(f, STYLE["link"])
    target(w, f'rack:power_estimate:watts{{device="{est}"}}'); override(w, STYLE["watte"])
    x = DEV_L + 8 + i * 124
    rect(f"acc_{f}", x, mon_top + 40, 4, 22, bgfield=f)
    rect(f"b_{f}", x + 4, mon_top + 40, 114, 22, text="  " + nm, size=10, bg=CHIPBG)
    rect(f"bv_{f}", x + 56, mon_top + 40, 62, 22, field=f, size=10,
         colorfield=f, align="right")
    ## The watt row sits UNDER its chip rather than inside it: the chip's 114 px
    ## already carries the name and the link state, and squeezing a third value
    ## in would truncate the name — which is the one thing that identifies which
    ## box you are looking at.
    rect(f"bw_{nm}", x + 4, mon_top + 62, 114, 16, field=w, size=9,
         colorfield=w, align="right")

# ============================================================ 3. the wall ===
rect("wall_hdr", CHIP_L, 26, CHIP_W, 18, text="BESIDE THE RACK", size=11, color=DIM)
## Starts where the header ENDS (26 + 18 = 44). It sat at y=42 until 2026-09-05,
## 2 px under the header's box — harmless while both lines are one row of
## text, and a real collision the day either wraps. The wall line starts at
## y=66, so there is room below.
rect("wall_sub", CHIP_L, 44, CHIP_W, 16, text="on the wall / a shelf — not racked",
     size=9, color=DIM)
rect("wall", WALL_X, 66, 3, 360, bg=WALLC)
for top, key, title, field, expr, sty, is_sub in WALL:
    h = 22 if is_sub else 26
    left = CHIP_L + (18 if is_sub else 0)
    w = CHIP_W - (18 if is_sub else 0)
    if not is_sub:
        rect(f"tick_{key}", WALL_X + 3, top + h // 2 - 1, CHIP_L - WALL_X - 3, 2, bg=WALLC)
    target(field, expr); override(field, STYLE[sty])
    rect(f"acc_{key}", left, top, 4, h, bgfield=field)
    rect(f"chip_{key}", left + 4, top, w - 4, h, text="  " + title,
         size=10 if is_sub else 11, bg=CHIPBG, color=INK)
    rect(f"v_{key}", left + w - 104, top, 98, h, field=field, size=10 if is_sub else 11,
         colorfield=field, align="right")
rect("gps_note", CHIP_L + 18, 358, CHIP_W - 18, 14,
     text="cable runs to the receiver on the patio", size=9, color=DIM)

## ------------------------------------------------ the right-hand column ---
## Readouts about the room, the power and the wireless. These used to be `stat`
## panels in a row underneath; folding them in here bought the rack ~20% more
## height, which is what makes the 42 units readable from across the cellar.
## Objects still get the chip-and-accent treatment, plain readings get a row.
RY = [TOP_Y]   # a running cursor so sections just append

def rlabel(text):
    RY[0] += 8
    rect(f"h_{text[:8]}", RCOL_L, RY[0], RCOL_W, 18, text=text, size=10, color=DIM)
    RY[0] += 20

## ⛔ The ACCESS POINTS columns are POSITIONAL: slot 0 is clients, slot 1 is
## uptime, slot 2 is PoE watts, on EVERY row. A row that lacks a value leaves
## THAT slot empty — it does not slide its other readings across to fill it.
## Until 2026-09-05 the one AP without PoE ("1st floor · fed elsewhere") went
## through its own two-value helper with its own geometry: its client count
## sat at x 1097–1185 in size 11 against 1083–1143 size 10 on the other four
## rows, and its UPTIME ended at x=1259, the column where every other row shows
## WATTS — a "days" figure standing in the watt column of a five-row block.
## One grid, one font size, and a missing value blanks its own column. (The
## rack rows do the OPPOSITE — flush right — on purpose; see section 2.)
AP_SLOTS = ((182, 60), (118, 56), (58, 52))   # (offset from the column's right edge, width)

def aprow(title, *entries):
    """One access point: up to three (field, expr, style) readings in the fixed
    clients / uptime / PoE-watts columns, entry i in slot i."""
    for f, e, sty in entries:
        target(f, e); override(f, STYLE[sty])
    rect(f"r_{entries[0][0]}", RCOL_L, RY[0], RCOL_W, 22, text="  " + title, size=10,
         bg=CHIPBG, color=INK)
    for (f, _, _), (off, w) in zip(entries, AP_SLOTS):
        rect(f"rv_{f}", RCOL_L + RCOL_W - off, RY[0], w, 22, field=f, size=10,
             colorfield=f, align="right")
    RY[0] += 24


def rrow(field, expr, title, style):
    target(field, expr); override(field, STYLE[style])
    rect(f"r_{field}", RCOL_L, RY[0], RCOL_W, 22, text="  " + title, size=10,
         bg=CHIPBG, color=INK)
    rect(f"rv_{field}", RCOL_L + RCOL_W - 150, RY[0], 144, 22, field=field,
         size=11, colorfield=field, align="right")
    RY[0] += 24

rlabel("ENVIRONMENT")
rrow("room_temp", hass("hass_sensor_temperature_celsius", "sensor.server_room_t_room_effective"), "room temp", "rtemp")
rrow("ruuvi", hass("hass_sensor_temperature_celsius", "sensor.carnotzetruuvi_temperature"), "ruuvi", "rtemp")
rrow("humidity", hass("hass_sensor_humidity_percent", "sensor.carnotzetruuvi_humidity"), "humidity", "hum")
rrow("fan", hass("hass_fan_speed_percent", "fan.fanspeed_fanspeed_server_room_exhaust"), "exhaust fan", "fan")
rrow("door", hass("hass_binary_sensor_state", "binary_sensor.carnotzet_door"), "door to garage", "door")

## No POWER section any more: the UPS row at U41-42 now carries battery,
## runtime, self-use and load together, and the wall chip carries the rack
## draw at the plug's own position. "rack average" was dropped (user,
## 2026-09-04) — it restated the wall chip, smoothed.

## The patch panel at U39 feeds the other four access points — garage plus the
## three floors above — so their client counts belong on this board too. The
## Aruba trio already exposed a per-client row (arubaClientSnr) and nobody was
## counting it; the MSM430 needed its client table added to the SNMP module.
rlabel("ACCESS POINTS  ·  clients")
## Which AP is where — user, 2026-09-01. The SNMP apName (APIN0505 etc.) is a
## device identity, not a location, and the mapping is recorded nowhere else:
##   .15 MSM430 = the rack itself   .212 APIN0315 = garage
##   .199 Zyxel = ground floor      .211 APIN0505 = 1st   .217 APIN0325 = 2nd
def upt(ip):  return f'sysUpTime{{instance="192.168.1.{ip}"}}/8640000'
def poe(port): return f'xikestor_poe_port_watts{{port="{port}"}}'
## Order is by LOCATION, top of the house down, then the two that are not on a
## floor (user, 2026-09-04): 2nd · 1st · ground · carnotzet · garage. The SNMP
## apName is a device identity, never a location, so this ordering exists only
## here and in the comment above.
aprow("2nd floor",
      ("ap_217", 'count(arubaClientSnr{instance="192.168.1.217"}) or vector(0)', "clients"),
      ("up_217", upt("217"), "days"), ("w_217", poe(3), "wattp"))
## 1st floor draws no PoE here — it hangs off a separate mid-house switch that
## also feeds the Hue and Ruuvi gateways, so port 2 is data only. Two entries,
## so the watt slot stays EMPTY on this row (see AP_SLOTS).
aprow("1st floor · fed elsewhere",
      ("ap_211", 'count(arubaClientSnr{instance="192.168.1.211"}) or vector(0)', "clients"),
      ("up_211", upt("211"), "days"))
## ⭐ The Zyxel is the odd one out: no association table at all, but a per-radio
## station count, so its clients are a SUM over radios rather than a row count.
aprow("ground floor",
      ("ap_zyxel", 'sum(zyxelRadioStations{instance="192.168.1.199"}) or vector(0)', "clients"),
      ("up_zyxel", upt("199"), "days"), ("w_zyxel", poe(1), "wattp"))
aprow("carnotzet · on the rack",
      ("ap_msm", f'count(msmClientRssi{{instance="{MSM}"}}) or vector(0)', "clients"),
      ("up_msm", upt("15"), "days"), ("w_msm", poe(5), "wattp"))
aprow("garage",
      ("ap_212", 'count(arubaClientSnr{instance="192.168.1.212"}) or vector(0)', "clients"),
      ("up_212", upt("212"), "days"), ("w_212", poe(4), "wattp"))

## ⭐ POWER ACCOUNTING. There WAS a power section here until 2026-09-04 and it
## was removed for restating the wall chip; this is not that section. These
## seven rows answer what the twin previously could not: the rack drew ~1.4 kW
## and the board could only account for the five servers, because the Cisco
## stack and everything behind the screen have no meter and no wattage over SNMP.
##
## Read it top down as one subtraction, with two guard rows in the middle that
## say whether the "metered" line can be TRUSTED:
##   UPS delivers        what the UPS reports sending to the load
##   metered             the five chassis (IPMI) + the PoE the XikeStor delivers
##   chassis reporting   guard: how many of the five the metered sum contains
##   chassis feed age    guard: seconds since the chassis probe last pushed
##   residual            delivers − metered = every unmetered thing in the rack
##   estimated           the datasheet figures in 47-rack-power-model.yml
##   model error         residual − estimated: the only line here that can be WRONG
##
## ⛔ The residual is a REMAINDER, so it also grows when a metered input goes
## missing — that is what "chassis reporting" is for. If it reads 4/5 the
## residual is overstated by a whole server and the model error will scream.
##
## ⚠️ Deliberately NOT started from the wall plug: the myStrom reads ~50 W more
## than the UPS delivers, and that gap is the UPS's own self-consumption, which
## the twin already shows separately on the U41-42 row. Subtracting from the wall
## would quietly bill that loss to the Cisco stack.
## ⛔ ALL THREE MEASURED ROWS ARE 6h AVERAGES and the label says so, because the
## rows are meant to SUBTRACT on screen: delivers − metered = residual. Mixing an
## instantaneous reading with an averaged one would leave three numbers that
## visibly do not add up. The 6h is not smoothing for looks — the chassis probe
## pushes every 15 min against a continuously-updating UPS entity, and the raw
## difference swung 313→408 W in four minutes with nothing physical changing.
rlabel("POWER  ·  metered vs modelled  ·  6h avg")
rrow("pwr_ups_out", "rack:power_ups_out:avg6h", "UPS delivers", "wattb")
rrow("pwr_metered", "rack:power_metered:avg6h", "metered · chassis + PoE", "wattb")
rrow("pwr_hosts", "rack:power_metered_hosts:count", "chassis reporting", "hosts")
## Not a 6h figure and not part of the subtraction — the one row here that says
## whether the metered inputs are still ARRIVING. See the `age` style for why
## the count above cannot answer that.
rrow("pwr_age", 'time() - push_time_seconds{job="chassis_health"}', "chassis feed age", "age")
rrow("pwr_resid", "rack:power_unmetered:avg6h", "residual · not metered", "wattr")
rrow("pwr_est", "rack:power_estimate_total:watts", "estimated for it", "watte")
rrow("pwr_err", "rack:power_model_error:avg6h", "model error", "werr")

## Zigbee keeps the chip treatment: it is a THING, not a reading. Health is the
## device roster — how many are talking — and it leaves green if any stops.
rlabel("ZIGBEE  ·  antenna mid-right of the rack")
## ⭐ BOTH counts guarded, and the guard cuts both ways. Until 2026-09-05 only
## zig_off had `or vector(0)`: on total Zigbee loss count(...==1) returns EMPTY,
## so "devices talking" went BLANK exactly when it should have read a red 0.
## The known trade-off in the other direction, NOT solved here: zig_off's guard
## means a total HA→VM outage — no hass_* series at all — renders a reassuring
## green "0 off". That wants the "stale/absent" treatment noted in section 2.
target("zig_up", f'count({ZIGBEE}==1) or vector(0)')
target("zig_off", f'count({ZIGBEE}==0) or vector(0)')
override("zig_up", STYLE["zigup"]); override("zig_off", STYLE["zigoff"])
rect("acc_zig", RCOL_L, RY[0], 4, 22, bgfield="zig_off")
rect("chip_zig", RCOL_L + 4, RY[0], RCOL_W - 4, 22, text="  devices talking", size=10, bg=CHIPBG)
rect("v_zigup", RCOL_L + RCOL_W - 150, RY[0], 80, 22, field="zig_up", size=11,
     colorfield="zig_up", align="right")
rect("v_zigoff", RCOL_L + RCOL_W - 66, RY[0], 60, 22, field="zig_off", size=11,
     colorfield="zig_off", align="right")

canvas = {
    "type": "canvas", "title": "", "id": 1, "transparent": True,
    ## No stat panels any more, so the canvas takes the whole budget: h=22 is
    ## 828 CSS against the ~845 the kiosk leaves at scale 1.3.
    "gridPos": {"h": 22, "w": 24, "x": 0, "y": 0},
    "datasource": DS, "targets": targets, "transformations": DROP_TIME,
    "options": {"inlineEditing": False, "showAdvancedTypes": False,
                "panZoom": False, "infinitePan": False,
                "root": {"type": "frame", "name": "root",
                         "constraint": {"horizontal": "left", "vertical": "top"},
                         "placement": {"top": 0, "left": 0, "width": CANVAS_W, "height": CANVAS_H},
                         "background": {"color": {"fixed": "transparent"}},
                         "border": {"color": {"fixed": "transparent"}},
                         "elements": elements}},
    "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [st(None, "text")]}},
                    "overrides": overrides},
}
panels = [canvas]

dash = {
    "title": "mdapi — Rack", "uid": "mdapi-rack",
    "description": ("Digital twin of the Carnotzet rack: a single canvas panel holding "
                    "the 42U drawing, the wall chips beside it and the readout column "
                    "to its right — no stat panels, they were folded into the canvas. "
                    "Generated from monitoring-grafana/dashboards/rack-twin-gen.py in "
                    "the fleet repo (~/rack-twin-gen.py is only a symlink to it): edit "
                    "the RACK/WALL maps there, never this JSON — CI fails if the two "
                    "drift. bone must run --force-device-scale-factor=1.3."),
    "tags": ["overview", "rack", "kiosk"], "timezone": "browser", "schemaVersion": 39,
    "editable": True, "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": []}, "annotations": {"list": []}, "panels": panels,
}

out = json.dumps(dash, indent=2)
if "--stdout" in sys.argv:
    print(out)
else:
    ## ⛔ RELATIVE TO THIS FILE, never `~/fleet`. This script used to hardcode
    ## the home checkout, so running it from a worktree or a second clone wrote
    ## the dashboard into ~/fleet behind your back — the edit landed in a
    ## different tree than the one you were about to commit. Resolving from
    ## __file__ means the copy you ran is the copy you updated.
    ##
    ## ⛔ realpath, NOT abspath — abspath does not follow symlinks. `~/rack-twin-gen.py`
    ## is a symlink to this file (kept so the old muscle-memory path still works),
    ## and under abspath its dirname is /home/tillo, so the script would have
    ## written /home/tillo/Overview/mdapi-rack.json — a brand-new file nobody
    ## looks at, while the committed dashboard silently stayed as it was. The
    ## failure is invisible: the script prints "wrote ..." and exits 0.
    dest = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                        "Overview", "mdapi-rack.json")
    open(dest, "w").write(out + "\n")
    print(f"wrote {dest}")
    print(f"  {len(panels)} panels, {len(elements)} canvas elements, "
          f"{sum(len(p['targets']) for p in panels)} queries")
