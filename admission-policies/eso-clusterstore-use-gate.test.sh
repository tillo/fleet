#!/usr/bin/env bash
# Behaviour test for the ValidatingAdmissionPolicy eso-clusterstore-use-gate
# (infra-p6q07.1). Server-side dry runs only: nothing is persisted.
#
#   ./eso-clusterstore-use-gate.test.sh            # case matrix
#   ./eso-clusterstore-use-gate.test.sh --replay   # + re-admit every live ES/PushSecret as fleet-agent
#
# Exit 0 = every case behaved as expected. Needs a cluster-admin context
# (impersonation). TENANT must hold `edit` in TENANT_NS and NOT the
# cluster-scoped `use` on clustersecretstores.
set -euo pipefail

CTX=${CTX:-mdapi-prod-direct}
TENANT=${TENANT:-u-b5wflbguvz}
TENANT_NS=${TENANT_NS:-tenant-1}
FLEET=${FLEET:-system:serviceaccount:cattle-fleet-system:fleet-agent}
k() { kubectl --context "$CTX" "$@"; }

fail=0
# expect <allow|deny> <description> <as> <manifest>
expect() {
  local want=$1 what=$2 as=$3 manifest=$4 out got
  if out=$(printf '%s\n' "$manifest" | k create --dry-run=server --as "$as" -f - 2>&1); then got=allow; else got=deny; fi
  # a Warn-mode policy admits but warns: count that as deny for the gate's verdict
  if [[ $got == allow && $out == *"eso-clusterstore-use-gate"* ]]; then got=deny; fi
  if [[ $got == "$want" ]]; then echo "ok   $want  $what"
  else echo "FAIL want=$want got=$got  $what"; echo "     ${out//$'\n'/ }"; fail=1; fi
}

es() { # es <name> <ns> <storeKind> <storeName> [<itemStoreKind> <itemStoreName>]
  local src=""
  [[ -n ${5:-} ]] && src=$'\n      sourceRef:\n        storeRef:\n          kind: '"$5"$'\n          name: '"$6"
  cat <<EOF
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: $1, namespace: $2}
spec:
  refreshInterval: 1h
  secretStoreRef: {kind: $3, name: $4}
  target: {name: $1}
  data:
    - secretKey: x
      remoteRef: {key: /mdapi/jump/gate-test}$src
EOF
}

ps() { # ps <name> <ns> <storeKind> <storeName>
  cat <<EOF
apiVersion: external-secrets.io/v1alpha1
kind: PushSecret
metadata: {name: $1, namespace: $2}
spec:
  secretStoreRefs: [{kind: $3, name: $4}]
  selector: {secret: {name: does-not-matter}}
  data:
    - match: {secretKey: x, remoteRef: {remoteKey: /mdapi/jump/gate-test}}
EOF
}

ps_selector() { # PushSecret that picks stores by label instead of by name
  cat <<EOF
apiVersion: external-secrets.io/v1alpha1
kind: PushSecret
metadata: {name: $1, namespace: $2}
spec:
  secretStoreRefs: [{kind: ClusterSecretStore, labelSelector: {matchLabels: {}}}]
  selector: {secret: {name: does-not-matter}}
  data:
    - match: {secretKey: x, remoteRef: {remoteKey: /mdapi/jump/gate-test}}
EOF
}

expect deny  "tenant ES -> ClusterSecretStore cm-akeyless"              "$TENANT" "$(es gate-t1 "$TENANT_NS" ClusterSecretStore cm-akeyless)"
expect deny  "tenant ES -> ClusterSecretStore openbao"                  "$TENANT" "$(es gate-t2 "$TENANT_NS" ClusterSecretStore openbao)"
expect deny  "tenant ES, own SecretStore but per-item CSS sourceRef"    "$TENANT" "$(es gate-t3 "$TENANT_NS" SecretStore mine ClusterSecretStore cm-akeyless)"
expect deny  "tenant PushSecret -> cm-akeyless"                         "$TENANT" "$(ps gate-t4 "$TENANT_NS" ClusterSecretStore cm-akeyless)"
expect deny  "tenant PushSecret -> stores by labelSelector"             "$TENANT" "$(ps_selector gate-t5 "$TENANT_NS")"
expect allow "tenant ES -> own namespaced SecretStore"                  "$TENANT" "$(es gate-t6 "$TENANT_NS" SecretStore mine)"
expect allow "fleet-agent ES -> cm-akeyless"                            "$FLEET"  "$(es gate-f1 "$TENANT_NS" ClusterSecretStore cm-akeyless)"
expect allow "fleet-agent PushSecret -> cm-akeyless"                    "$FLEET"  "$(ps gate-f2 "$TENANT_NS" ClusterSecretStore cm-akeyless)"

# The tenant's namespace already holds a Fleet-shipped ExternalSecret on
# cm-akeyless: re-pointing its key must be refused, a spec-neutral edit not.
# expect_update <allow|deny> <description> <jq filter>
expect_update() {
  local want=$1 what=$2 filter=$3 name obj out got
  name=$(k -n "$TENANT_NS" get externalsecrets.external-secrets.io -o json \
    | jq -r '[.items[]|select(.spec.secretStoreRef.kind=="ClusterSecretStore")][0].metadata.name')
  if [[ -z $name || $name == null ]]; then echo "skip $what (no CSS-backed ES in $TENANT_NS)"; return; fi
  obj=$(k -n "$TENANT_NS" get externalsecrets.external-secrets.io "$name" -o json | jq "del(.status,.metadata.managedFields) | $filter")
  if out=$(printf '%s' "$obj" | k replace --dry-run=server --as "$TENANT" -f - 2>&1); then got=allow; else got=deny; fi
  if [[ $got == allow && $out == *"eso-clusterstore-use-gate"* ]]; then got=deny; fi
  if [[ $got == "$want" ]]; then echo "ok   $want  $what"
  else echo "FAIL want=$want got=$got  $what"; echo "     ${out//$'\n'/ }"; fail=1; fi
}
expect_update deny  "tenant re-points the key of a Fleet ES in its ns" '.spec.data[0].remoteRef.key="/mdapi/jump/gate-test"'
expect_update allow "tenant adds an annotation to a Fleet ES in its ns" '.metadata.annotations["gate-test/touch"]="1"'

if [[ ${1:-} == --replay ]]; then
  # Every live object must still be admitted for its real author (Fleet) and
  # for the ESO controller's metadata-only updates (finalizers).
  n=0; bad=0
  while read -r kind ns name; do
    n=$((n+1))
    obj=$(k -n "$ns" get "$kind" "$name" -o json | jq 'del(.status,.metadata.managedFields,.metadata.creationTimestamp,.metadata.generation,.metadata.uid,.metadata.resourceVersion)')
    if ! out=$(printf '%s' "$obj" | k replace --dry-run=server --as "$FLEET" -f - 2>&1) || [[ $out == *"eso-clusterstore-use-gate"* ]]; then
      echo "FAIL replay fleet-agent $kind $ns/$name: ${out//$'\n'/ }"; bad=1; fi
    obj2=$(printf '%s' "$obj" | jq '.metadata.annotations["gate-test/touch"]="1"')
    if ! out=$(printf '%s' "$obj2" | k replace --dry-run=server --as system:serviceaccount:external-secrets:external-secrets -f - 2>&1) || [[ $out == *"eso-clusterstore-use-gate"* ]]; then
      echo "FAIL replay eso metadata-only $kind $ns/$name: ${out//$'\n'/ }"; bad=1; fi
  done < <(k get externalsecrets.external-secrets.io,pushsecrets.external-secrets.io -A -o json | jq -r '.items[]|[.kind,.metadata.namespace,.metadata.name]|@tsv')
  echo "replay: $n objects, $([[ $bad == 0 ]] && echo ok || echo FAIL)"
  [[ $bad == 0 ]] || fail=1
fi

exit $fail
