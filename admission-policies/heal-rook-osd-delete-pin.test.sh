#!/usr/bin/env bash
# Behaviour test for the ValidatingAdmissionPolicy heal-rook-osd-delete-pin.
#
# A DELETE of a missing pod answers NotFound before admission, so this deletes LIVE
# pods with --dry-run=server: admission runs, nothing is deleted. As the heal SA an
# OSD pod must be admitted and a mon / mgr / toolbox pod refused; cluster-admin is
# not fenced.
#
#   ./heal-rook-osd-delete-pin.test.sh
#
# Exit 0 = every case behaved as expected. Needs a cluster-admin context.
set -euo pipefail

CTX=${CTX:-mdapi-prod-direct}
HEAL=system:serviceaccount:windmill:windmill-worker-heal
k() { kubectl --context "$CTX" "$@"; }
pod() { k -n rook-ceph get pod -l "app=$1" -o jsonpath='{.items[0].metadata.name}'; }

fail=0
verdict() { # verdict <want> <what> <output>
  local got
  if [[ $3 == *"heal-rook-osd-delete-pin"* ]]; then got=deny
  elif [[ $3 == *"(server dry run)"* ]]; then got=allow
  else got=other; fi
  if [[ $got == "$1" ]]; then echo "ok   $1  $2"
  else echo "FAIL want=$1 got=$got  $2"; echo "     ${3//$'\n'/ }" | cut -c1-300; fail=1; fi
}
del() { # del <want> <app label>   (as the heal SA)
  local p; p=$(pod "$2")
  verdict "$1" "heal delete $2 ($p)" \
    "$(k --as "$HEAL" -n rook-ceph delete pod "$p" --dry-run=server 2>&1 || true)"
}

del allow rook-ceph-osd
del deny  rook-ceph-mon
del deny  rook-ceph-mgr
del deny  rook-ceph-tools
del deny  rook-ceph-rgw
p=$(pod rook-ceph-mon)
verdict allow "cluster-admin delete rook-ceph-mon ($p)" \
  "$(k -n rook-ceph delete pod "$p" --dry-run=server 2>&1 || true)"

exit $fail
