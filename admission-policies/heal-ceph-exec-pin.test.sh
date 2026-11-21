#!/usr/bin/env bash
# Behaviour test for the ValidatingAdmissionPolicy heal-ceph-exec-pin.
#
# Admission runs BEFORE the apiserver looks the pod up, so a raw exec request
# against a pod that does not exist exercises the policy without running
# anything: refused -> the policy's message, admitted -> NotFound. That lets
# the mutating ceph commands be tested without touching the cluster. One real
# read-only exec (`ceph -s -f json`) into the live toolbox proves end to end.
#
#   ./heal-ceph-exec-pin.test.sh
#
# Exit 0 = every case behaved as expected. Needs a cluster-admin context.
set -euo pipefail

CTX=${CTX:-mdapi-prod-direct}
HEAL=system:serviceaccount:windmill:windmill-worker-heal
GHOST=rook-ceph-tools-zz-pin-test   # matches the toolbox name prefix, never exists
k() { kubectl --context "$CTX" "$@"; }

fail=0
verdict() { # verdict <want> <what> <output>
  local got
  if [[ $3 == *"heal-ceph-exec-pin"* ]]; then got=deny
  elif [[ $3 == *"NotFound"* || $3 == *"not found"* ]]; then got=allow
  else got=other; fi
  if [[ $got == "$1" ]]; then echo "ok   $1  $2"
  else echo "FAIL want=$1 got=$got  $2"; echo "     ${3//$'\n'/ }" | cut -c1-300; fail=1; fi
}
# raw <want> <what> <ns> <pod> <extra query> <argv...>
raw() {
  local want=$1 what=$2 ns=$3 pod=$4 q="stdout=1&stderr=1$5" a; shift 5
  for a in "$@"; do q+="&command=$(jq -rn --arg a "$a" '$a|@uri')"; done
  verdict "$want" "$what" "$(k --as "$HEAL" create --raw \
    "/api/v1/namespaces/$ns/pods/$pod/exec?$q" -f /dev/null 2>&1 || true)"
}
ceph() { raw "$1" "$2" rook-ceph "$GHOST" "&container=rook-ceph-tools" /bin/sh -c "$3"; }

# --- every command the three heal scripts send ---------------------------
# ceph_osd_stepped_fill
ceph allow "osd tree json"        "ceph osd tree -f json"
ceph allow "osd perf json"        "ceph osd perf -f json"
ceph allow "osd dump json"        "ceph osd dump -f json"
ceph allow "status json"          "ceph -s -f json"
ceph allow "set norebalance"      "ceph osd set norebalance"
ceph allow "unset norebalance"    "ceph osd unset norebalance"
ceph allow "crush reweight"       "ceph osd crush reweight osd.4 1.25209"
ceph allow "crush reweight 1e-05" "ceph osd crush reweight osd.4 1e-05"
# ceph_mclock_enforcer
ceph allow "config set balanced"  "ceph config set osd osd_mclock_profile balanced"
ceph allow "tell high_client_ops" "ceph tell osd.* config set osd_mclock_profile high_client_ops"
ceph allow "config get mclock"    "ceph config get osd osd_mclock_profile"
# ceph_noout_release (kubernetes client: "sh", no container= given)
raw allow "noout: read flags" rook-ceph "$GHOST" "" sh -c "ceph osd dump 2>/dev/null | grep '^flags'"
raw allow "noout: unset"      rook-ceph "$GHOST" "" sh -c "ceph osd unset noout 2>&1"

# --- what a stolen heal token would want ---------------------------------
ceph deny "keyring dump"          "ceph auth get client.admin"
ceph deny "pool delete"           "ceph osd pool delete rbd rbd --yes-i-really-really-mean-it"
ceph deny "allowed + ; chain"     "ceph -s -f json; ceph auth ls"
ceph deny "allowed + newline"     $'ceph -s -f json\nceph auth ls'
ceph deny "reweight injection"    "ceph osd crush reweight osd.4 1.0;id"
ceph deny "other mclock profile"  "ceph config set osd osd_mclock_profile custom"
ceph deny "arbitrary shell"       "id"
raw  deny "direct argv"       rook-ceph "$GHOST" "&container=rook-ceph-tools" ceph auth ls
raw  deny "stdin"             rook-ceph "$GHOST" "&container=rook-ceph-tools&stdin=1" /bin/sh -c "ceph -s -f json"
raw  deny "tty"               rook-ceph "$GHOST" "&container=rook-ceph-tools&tty=1" /bin/sh -c "ceph -s -f json"
raw  deny "privileged OSD pod" rook-ceph rook-ceph-osd-0-zz "" /bin/sh -c "ceph -s -f json"
raw  deny "other container"   rook-ceph "$GHOST" "&container=sidecar" /bin/sh -c "ceph -s -f json"

# --- nobody and nowhere else is fenced -----------------------------------
raw allow "heal exec outside rook-ceph" mail nope-zz "" id
verdict allow "cluster-admin in rook-ceph" "$(k create --raw \
  "/api/v1/namespaces/rook-ceph/pods/$GHOST/exec?stdout=1&command=id" -f /dev/null 2>&1 || true)"

# --- end to end: one real read-only exec into the live toolbox ------------
TOOLS=$(k -n rook-ceph get pod -l app=rook-ceph-tools -o jsonpath='{.items[0].metadata.name}')
if out=$(k --as "$HEAL" -n rook-ceph exec "$TOOLS" -c rook-ceph-tools -- /bin/sh -c "ceph -s -f json" 2>&1) \
   && jq -e .fsid <<<"$out" >/dev/null; then echo "ok   allow  live toolbox ceph -s"
else echo "FAIL live toolbox ceph -s: ${out:0:200}"; fail=1; fi

exit $fail
