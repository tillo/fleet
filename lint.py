#!/usr/bin/env python3
"""
Fleet manifest lint — catches two classes of foot-gun that have caused real
outages on this cluster:

  (A) Keel policy=force without match-tag — lets Keel jump to ANY newer
      tag in the repo. Caused the 2026-05-16 mail outage when the
      `:cache` BuildKit blob got pulled instead of `:latest`.

  (B) comparePatches strips image on containers/0 but the Deployment has
      additional containers that ARE also Keel-managed (sidecars). Caused
      persistent Fleet drift on tv/sftpgo.

Exit code is non-zero if any check fails. Run locally or wire into CI.
"""

import sys, pathlib, re, json

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML required. pip install pyyaml\n")
    sys.exit(2)

REPO = pathlib.Path(__file__).resolve().parent
errors: list[str] = []
warnings: list[str] = []


def all_yaml_files() -> list[pathlib.Path]:
    return [
        p for p in REPO.rglob("*.y*ml")
        if not any(part.startswith(".") for part in p.relative_to(REPO).parts)
    ]


def load_docs(path: pathlib.Path):
    try:
        return list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError as e:
        warnings.append(f"{path.relative_to(REPO)}: YAML parse error: {e}")
        return []


def strip_yaml_comments(text: str) -> str:
    """Drop YAML comments so prose that merely *mentions* an annotation (e.g. a
    `# keel.sh/policy: force` explainer in a fleet.yaml header) doesn't trip the
    raw-substring checks. Quote-aware, and per the YAML spec a `#` only starts a
    comment at line start or after whitespace — so `path: /a#b` is preserved."""
    out = []
    for line in text.splitlines():
        in_s = in_d = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == "'" and not in_d:
                in_s = not in_s
            elif ch == '"' and not in_s:
                in_d = not in_d
            elif ch == "#" and not in_s and not in_d and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Check A — Keel policy=force MUST have match-tag: "true"
# ---------------------------------------------------------------------------
# Grep-level on comment-stripped text: keel annotations may live inside Helm
# values (arbitrary depth) so a full YAML walk would have to know the chart
# schema. Substring grep is good enough; stripping comments first avoids the
# known false positive of prose that documents the annotation without using it.
RE_FORCE = re.compile(r"keel\.sh/policy:\s*[\"']?force[\"']?")
RE_MATCH = re.compile(r"keel\.sh/match-tag:\s*[\"']?true[\"']?")

for path in all_yaml_files():
    text = strip_yaml_comments(path.read_text())
    if RE_FORCE.search(text) and not RE_MATCH.search(text):
        errors.append(
            f"[KEEL-FORCE-NO-MATCH-TAG] {path.relative_to(REPO)}: "
            'has `keel.sh/policy: force` but no `keel.sh/match-tag: "true"`. '
            "Keel will jump to any newer tag in the repo (mail outage 2026-05-16)."
        )


# ---------------------------------------------------------------------------
# Check B — comparePatches stripping containers/0/image while the
# Deployment has additional containers
# ---------------------------------------------------------------------------
# Find every fleet.yaml that strips `/spec/template/spec/containers/0/image`,
# then look at sibling files (same directory and below) for the named
# Deployment. If that Deployment has >1 container, warn — the additional
# containers may also be Keel-tracked and need their own op:remove.

def find_deployment(start: pathlib.Path, name: str, namespace: str | None):
    """Search start dir + subdirs for a Deployment with matching name."""
    for cand in start.rglob("*.y*ml"):
        if cand.name in ("fleet.yaml", "fleet.yml"):
            continue
        for doc in load_docs(cand):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") != "Deployment":
                continue
            md = doc.get("metadata", {})
            if md.get("name") != name:
                continue
            if namespace and md.get("namespace") and md["namespace"] != namespace:
                continue
            return cand, doc
    return None, None


for fleet_yaml in REPO.rglob("fleet.y*ml"):
    docs = load_docs(fleet_yaml)
    if not docs:
        continue
    root = docs[0] if isinstance(docs[0], dict) else {}
    patches = (root.get("diff") or {}).get("comparePatches") or []
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        if patch.get("kind") != "Deployment":
            continue
        ops = patch.get("operations") or []
        # collect every "op:remove" on /spec/template/spec/containers/N/image
        stripped_idxs = set()
        for op in ops:
            if not isinstance(op, dict):
                continue
            if op.get("op") != "remove":
                continue
            m = re.fullmatch(
                r"/spec/template/spec/containers/(\d+)/image", op.get("path", "")
            )
            if m:
                stripped_idxs.add(int(m.group(1)))
        if not stripped_idxs:
            continue
        cand, dep = find_deployment(
            fleet_yaml.parent, patch.get("name", ""), patch.get("namespace")
        )
        if not dep:
            continue
        containers = (
            dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        all_idxs = set(range(len(containers)))
        missing = sorted(all_idxs - stripped_idxs)
        if missing:
            missing_names = [containers[i].get("name", f"#{i}") for i in missing]
            errors.append(
                f"[COMPAREPATCH-MULTI-CONTAINER] {fleet_yaml.relative_to(REPO)}: "
                f"comparePatch for Deployment/{patch['name']} strips image on "
                f"containers{sorted(stripped_idxs)} but the Deployment "
                f"({cand.relative_to(REPO)}) has {len(containers)} containers; "
                f"missing: {missing_names}. "
                "If any of those containers are Keel-tracked, this bundle will "
                "stay Modified (sftpgo-auth drift 2026-05-16)."
            )


# ---------------------------------------------------------------------------
# Check C — image-policy 2-lane convention (postmortem A2 / image-policy P4)
# ---------------------------------------------------------------------------
# Static port of ~/imagepolicy-study/image-policy-lint.py (that one is kubectl-
# based). This runs in fleet CI with no cluster access, so it can only see RAW
# Deployment/StatefulSet/DaemonSet manifests in this repo — NOT workloads whose
# imagePullPolicy lives in Helm values (longhorn, democratic-csi, any chart).
# Audit those with the kubectl lint.
#
# Lanes (locked 2026-06-11, ~/imagepolicy-study/PROPOSAL.md):
#   Lane A (apps):               floating tag + Always       (Keel-driven)
#   Lane B (bootstrap-critical): pinned tag  + IfNotPresent  (Renovate-driven)
# Bad combos: floating+IfNotPresent (never updates) and pinned+Always (re-pulls a
# fixed tag every start; can't boot when the registry itself is down — the
# 2026-06-06 deadlock). Bootstrap-critical MUST be Lane B => hard error. App-tier
# drift is report-only (warning) until the P3 rollout lands; promote to error after.
# Omitted imagePullPolicy is scored at the k8s default (floating->Always,
# pinned->IfNotPresent), so manifests that simply leave it out are NOT flagged.

C_FLOATING = re.compile(r"^(latest|stable|edge|main|master|develop|dev|nightly|"
                        r"rolling|full|alpine|[0-9]+-alpine)$")
C_PINNED = re.compile(r"^v?[0-9]+([._-].*)?$|^[0-9]{8}")
C_CRIT_IMG = ("technitium/dns-server", "project-zot/zot", "longhornio/",
              "democratic-csi", "/mdapi/nameserver", "/mdapi/unbound", "dxflrs/garage")
C_CRIT_NAME = {"technitium-primary", "technitium-secondary", "nameserver",
               "unbound", "zot", "garage"}
C_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


def c_tag_class(image: str) -> str:
    image = str(image)
    if "@sha256:" in image:
        return "digest"
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return "floating"            # no tag => :latest
    tag = image.rsplit(":", 1)[-1]
    if C_FLOATING.match(tag):
        return "floating"
    if C_PINNED.match(tag):
        return "pinned"
    return "other"


for path in all_yaml_files():
    for doc in load_docs(path):
        if not isinstance(doc, dict) or doc.get("kind") not in C_WORKLOAD_KINDS:
            continue
        md = doc.get("metadata", {}) or {}
        name = md.get("name", "?")
        spec = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
        for c in spec.get("containers", []) or []:
            if not isinstance(c, dict):
                continue
            img = c.get("image")
            if not img or "${" in str(img) or "{{" in str(img):
                continue                       # templated/var image, can't classify
            pol = c.get("imagePullPolicy", "")
            cls = c_tag_class(img)
            # effective policy: score omitted pullPolicy at the k8s default
            eff = pol or ("IfNotPresent" if cls in ("pinned", "digest") else "Always")
            laneB = cls in ("pinned", "digest") and eff == "IfNotPresent"
            crit = name in C_CRIT_NAME or any(s in str(img) for s in C_CRIT_IMG)
            where = f"{path.relative_to(REPO)}: {name}/{c.get('name', '?')}"
            if crit:
                if not laneB:
                    errors.append(
                        f"[IMG-LANE-CRITICAL] {where}: bootstrap-critical workload "
                        f"must be Lane B (pinned tag + IfNotPresent); got "
                        f"{cls}+{eff}: {img}. A cold cluster can't pull this when "
                        "the registry is down."
                    )
            elif cls == "floating" and eff == "IfNotPresent":
                warnings.append(
                    f"[IMG-LANE] {where}: floating tag + IfNotPresent never updates "
                    f"(Lane A wants Always; Lane B wants a pinned tag): {img}"
                )
            elif cls in ("pinned", "digest") and eff == "Always":
                warnings.append(
                    f"[IMG-LANE] {where}: pinned tag + Always re-pulls a fixed tag "
                    f"every start and can't boot if the registry is down "
                    f"(-> IfNotPresent): {img}"
                )


# ---------------------------------------------------------------------------
# Check D — monitoring-rules alert label contract (black-hole guard)
# ---------------------------------------------------------------------------
# The mdapi Alertmanager parent route is a deliberate black-hole: an alert
# without team=mdapi (mdapi-watchdog for the deadman) is evaluated, fires, and
# is silently dropped at routing. 94 alerts — the entire imported Ceph pack
# among them — sat in that state until 2026-07-06. This check makes the label
# contract structural. Taxonomy source of truth: monitoring-rules/_README.md.

RULE_COMPONENTS = {
    # statuspage-mapped
    "Mail", "Sign-in (SSO)", "Files & Documents", "Websites", "DNS",
    "Internet", "Smart Home",
    # internal-only
    "Platform", "Storage", "Backups", "Logging", "NTP",
}
RULE_SEVERITIES = {"critical", "warning", "info"}
RULE_TEAMS = {"mdapi", "mdapi-watchdog"}
# component inherited from series labels (blackbox Probe targetLabels):
RULE_COMPONENT_EXEMPT = {"SyntheticProbeFailing", "SyntheticProbeSlowResponse",
                         "SyntheticProbeTLSExpiringSoon"}
# the deadman: severity none by design, routed by its own VMAC on team:
RULE_FULL_EXEMPT = {"WatchdogMdapi"}

_seen_prefix: dict[str, str] = {}
for path in sorted((REPO / "monitoring-rules").glob("*.yml")):
    m = re.match(r"^(\d+[a-z]?)-", path.name)
    if m:
        if m.group(1) in _seen_prefix:
            errors.append(
                f"[RULES-NUMBERING] monitoring-rules/{path.name} collides with "
                f"{_seen_prefix[m.group(1)]} on prefix {m.group(1)} — pick the "
                "next free number (letter suffix only for a related sub-series)."
            )
        else:
            _seen_prefix[m.group(1)] = path.name
    for doc in load_docs(path):
        if not isinstance(doc, dict) or doc.get("kind") != "PrometheusRule":
            continue
        if doc.get("metadata", {}).get("labels", {}).get("monitoring-stack") != "mdapi":
            errors.append(
                f"[RULES-SELECTOR] monitoring-rules/{path.name}: PrometheusRule "
                f"{doc.get('metadata', {}).get('name')} lacks `monitoring-stack: "
                "mdapi` — vmalert will never load it."
            )
        for grp in (doc.get("spec", {}).get("groups") or []):
            for rule in (grp.get("rules") or []):
                if "alert" not in rule:
                    continue
                name = rule["alert"]
                lab = rule.get("labels") or {}
                where = f"monitoring-rules/{path.name}: alert {name}"
                if lab.get("team") not in RULE_TEAMS:
                    errors.append(
                        f"[RULES-TEAM] {where}: team={lab.get('team')!r} — the "
                        "parent route black-holes it; nobody will ever be notified."
                    )
                if name in RULE_FULL_EXEMPT:
                    continue
                if lab.get("severity") not in RULE_SEVERITIES:
                    errors.append(
                        f"[RULES-SEVERITY] {where}: severity={lab.get('severity')!r} "
                        f"not in {sorted(RULE_SEVERITIES)}."
                    )
                if name not in RULE_COMPONENT_EXEMPT and lab.get("component") not in RULE_COMPONENTS:
                    errors.append(
                        f"[RULES-COMPONENT] {where}: component={lab.get('component')!r} "
                        "not in the _README.md taxonomy (statuspage mapping + "
                        "Pushover grouping key on it)."
                    )
                # Optional statuspage opt-out: the adapter skips alerts labeled
                # statuspage=exclude. Any other value is a typo that would
                # silently keep degrading the public page — fail it here.
                if "statuspage" in lab and lab["statuspage"] != "exclude":
                    errors.append(
                        f"[RULES-STATUSPAGE] {where}: statuspage={lab['statuspage']!r} "
                        "— the only supported value is 'exclude' (adapter opt-out)."
                    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
for w in warnings:
    print(f"WARN  {w}")
for e in errors:
    print(f"FAIL  {e}")

if errors:
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).")
    sys.exit(1)
print(f"OK — {len(warnings)} warning(s), 0 errors.")
