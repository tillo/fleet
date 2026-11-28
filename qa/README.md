# QA cluster bundles

The IAST/DAST QA cluster (bead infra-7rog.41.6) runs on Rackspace Spot, outside
Rancher and outside this estate's network. A standalone Fleet controller inside that
cluster renders exactly the directories in `qa-paths.txt`; `gitrepo/mdapi-prod`
renders exactly `fleet-paths.txt`. A bundle is listed in one and never the other.
`tests/test_qa_bundles_never_reach_prod.py` fails the MR if that is violated.

## ⛔ Data policy (hard rule, user decision 2026-11-23)

Only **SYNTHETIC or ANONYMISED** data may ever reach this cluster.

- Anonymise on our own hardware, then export; never anonymise in place here.
- **Pseudonymised data is NOT anonymised** and must never reach this cluster.
- Real data — customer or personal, any amount, any form — never reaches this cluster.
- No production Secret, credential or kubeconfig is shipped from any `qa/` bundle.
  The only credentials a QA pod gets are ones minted for QA (the zot read-only
  pull user, the IAST sink, Keycloak test-realm clients, QA Horizon API keys) --
  with ONE named exception, below.

## ⚠️ The one exception: the Horizon licence (user decision 2026-11-25)

A deliberate, named exception to "no prod secrets in QA" and to the storage boundary
(Spot is third-party), for **exactly two items and nothing else from prod**
(beads infra-7rog.41.12 / .41.11):

| item | prod source | in QA |
|---|---|---|
| Horizon LICENCE | Akeyless `/kyos/horizon/license/license` (prod: ESO `horizon-license`) | Secret `horizon/horizon-license`, label `mdapi.ch/prod-value-exception=infra-7rog.41.12` |
| registry.evertrust.io pull credential | Akeyless `/kyos/evertrust/registry/dockerconfigjson` | **not shipped**: QA runs the estate's image from zot, and a fresh install runs no horizon-migration Job (prod's only consumer) |

Delivery: the QA cluster has no External Secrets Operator, and installing one would put
an Akeyless identity inside the third-party cluster. So `~/src/spot-qa/horizon-qa-secrets.py`
reads the licence in-process on the seat and pipes it to the QA API server on stdin: no
file, no argv, no transcript, and nothing left in QA that could fetch another item.
Never in git (`tests/test_qa_horizon_stays_isolated.py` fails on any Secret or
ExternalSecret in `qa/horizon`).

What the exception does NOT cover, and stays out: the vault master key (QA uses a
throwaway local Tink keyset; the `ciphertrust://` hook is never exercised and there is no
network path from Spot to the prod CipherTrust Manager), prod Horizon data or API keys,
prod Keycloak, the Evertrust Stream licence, and the Kustodyan RPS client credentials
(kustodyan-mcp stays out of QA). Widening it needs a new user decision.

Teardown: delete Secret `horizon/horizon-license` (or the cloudspace) when the QA
Horizon is retired.

Why: CLAUDE.md's storage boundary keeps customer data on our own systems. This cluster
is a third-party control plane and a third-party node pool, so the rule is enforced
here, in the repo, rather than by remembering.

## Bundles

| directory | what it is |
|---|---|
| `qa-baseline` | namespaces, default-deny egress, zot pull Secret, the datadog deny |
| `qa-synthetic-test` | proof bundle: a synthetic-data ConfigMap, no image needed |
| `iast-sink` | the IAST sink (copy of `../iast-sink`, pinned to it by `tests/test_qa_iast_sink_matches_prod.py`) + the read-only `iast-sarif-reader` identity DefectDojo's sync uses through the API server |
| `bussola` | Bussola with the IAST tracer on, an emptyDir Postgres with no password, synthetic data typed in by the QA traffic only, fenced to DNS + its DB + the sink |
| `backstage`, `keycloak`, `paperless-kustodyan` | wave-1 IAST copies (tracer on + `-off` control), synthetic data, each fenced to DNS + its own stores + the sink |
| `evertrust-mcp` | horizon-mcp + stream-mcp with the tracer on, pointed at the QA Horizon with a QA-minted identity (`qa-mcp`); kustodyan-mcp stays out |
| `horizon` | Evertrust Horizon (the estate's `horizon-ciphertrust` image) with the tracer on + `horizon-off`; emptyDir MongoDB, throwaway Tink keyset, the prod licence (exception above) |
| `spa-dast` | SPA-capable DAST for the keycloak and backstage copies (infra-7rog.31.24): browser-spider plans, per-plugin OpenAPI import, a Playwright journey, all as SUSPENDED CronJobs -- Fleet keeps the templates and nothing runs by itself |
