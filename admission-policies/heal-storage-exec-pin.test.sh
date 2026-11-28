#!/usr/bin/env bash
# Behaviour test for the ValidatingAdmissionPolicy heal-storage-exec-pin
# (infra-p6q07.2). An exec CONNECT cannot be dry-run, so this runs REAL execs,
# impersonating the heal-storage SA, into one harmless target pod; the only
# command that may get through is a read-only `ls` of /tmp.
#
#   ./heal-storage-exec-pin.test.sh
#
# Exit 0 = every case behaved as expected. Needs a cluster-admin context.
set -euo pipefail

CTX=${CTX:-mdapi-prod-direct}
NS=${NS:-jump}
POD=${POD:-$(kubectl --context "$CTX" -n "$NS" get pod -l app=jump-bastion -o name | head -1)}
SA=system:serviceaccount:windmill:windmill-worker-heal-storage
k() { kubectl --context "$CTX" "$@"; }

fail=0
# expect <allow|deny> <description> [-i] -- <command...>
expect() {
  local want=$1 what=$2 out rc=0 got; shift 2
  out=$(k --as "$SA" -n "$NS" exec "$POD" "$@" </dev/null 2>&1) || rc=$?
  if [[ $out == *"heal-storage-exec-pin"* ]]; then got=deny
  elif [[ $rc == 0 ]]; then got=allow
  else got="error($rc)"; fi
  if [[ $got == "$want" ]]; then echo "ok   $want  $what"
  else echo "FAIL want=$want got=$got  $what"; echo "     ${out//$'\n'/ }" | cut -c1-300; fail=1; fi
}

# The exact shapes longhorn_eio_autoheal.fs_probe sends (shlex.quote leaves a
# safe path bare and single-quotes any other).
expect allow "probe, bare path"   -- sh -c "timeout 5 ls -la /tmp /tmp/*/ 2>&1 | head -200"
expect allow "probe, quoted path" -- sh -c "timeout 5 ls -la '/tmp' '/tmp'/*/ 2>&1 | head -200"

expect deny  "arbitrary command"  -- id
expect deny  "probe + ; id"       -- sh -c "timeout 5 ls -la /tmp /tmp/*/ 2>&1 | head -200; id"
expect deny  "command substitution in path" -- sh -c "timeout 5 ls -la /\$(id) /tmp/*/ 2>&1 | head -200"
expect deny  "newline injection"  -- sh -c $'timeout 5 ls -la /tmp /tmp/*/ 2>&1 | head -200\nid'
expect deny  "probe with stdin"   -i -- sh -c "timeout 5 ls -la /tmp /tmp/*/ 2>&1 | head -200"
expect deny  "bash instead of sh" -- bash -c "timeout 5 ls -la /tmp /tmp/*/ 2>&1 | head -200"

# Nobody else is fenced: cluster-admin still execs anything.
if k -n "$NS" exec "$POD" -- true; then echo "ok   allow  cluster-admin unaffected"
else echo "FAIL cluster-admin exec refused"; fail=1; fi

exit $fail
