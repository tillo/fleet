# fleet

Rancher Fleet GitOps repository for **MDAPI** — a self-hosted, multi-cluster Kubernetes platform built and operated as a hands-on showcase of production-grade infrastructure patterns at homelab scale.

[**Documentation: docs.mdapi.ch**](https://docs.mdapi.ch) · [**MDAPI on LinkedIn**](https://www.linkedin.com/company/mdapi/)

---

## What is MDAPI?

MDAPI is a personal infrastructure project run with the discipline and tooling of a production environment. The premise: the *operating model* of a serious platform — declarative state, GitOps reconciliation, federated multi-cluster control, self-healing automation, observability, secrets management, identity-aware access — is not a function of headcount or scale. It can be expressed at the size of a single home and a small cluster of bare-metal nodes if you choose to.

The point isn't what MDAPI runs. The point is *how* it's run, and that the architecture, operational model, and recovery story are real.

## What this repository is

This repository is the **delivery surface**: a Rancher Fleet GitOps store. Every workload declared here is continuously reconciled across the clusters Fleet targets — bare-metal production at home, plus cloud-hosted development and test clusters that share the same control plane. Pushes to `main` apply automatically; drift is reverted; bundle health is observable.

The repository is intentionally narrow. It carries manifests, not reasoning. Architecture choices, design decisions, operational runbooks, and lessons learned all live in [docs.mdapi.ch](https://docs.mdapi.ch).

## The broader picture

What sits around this repository:

- **An edge router** built from a custom OpenWrt fork on bare hardware, terminating an XGS-PON fiber connection, routing between trusted-LAN/DMZ/admin VLANs, hosting split-horizon DNS, and pushing every interesting kernel signal to Home Assistant. Self-healing watchdogs at the OS, network, and Home Assistant layers recover from optical-layer events, kernel stalls, and service flaps without human intervention.
- **A control plane** anchored on Rancher, managing a local bare-metal Kubernetes cluster (RKE2 on a Harvester HCI substrate that also hosts virtual machines) and cloud-hosted federated clusters used as deployment targets.
- **A platform layer** providing block storage with declarative snapshot and backup policy, an internal DNS authority that participates directly in ACME DNS-01 issuance, automated TLS, S3-compatible object storage, a self-hosted source forge and CI, centralized identity over OIDC, and an authentication gateway in front of every exposed surface.
- **An operational layer** providing structured logging and alerting, scheduled job orchestration, and CI/CD pipelines that build, scan, and continuously patch the workloads running on the platform.

If any of the above is interesting, [the docs](https://docs.mdapi.ch) are the right next click.

## License & reuse

The contents of this repository are made public for inspection and reference. Individual workloads, charts, and patches retain their upstream licenses; nothing here grants additional rights to upstream code. Configuration that is original to this project may be adapted as long as origin is credited.
