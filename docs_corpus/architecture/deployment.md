# Deployment Architecture

## Overview

All services run as containers on Kubernetes. We use Amazon EKS with two node
groups: `general` (on-demand, m6i.large) for stateless services and `jobs`
(spot instances) for batch workloads. Each service ships its own Dockerfile
and Helm chart; charts live in the service repository under `deploy/`.

## Environments

| Environment | Cluster     | Purpose                                              |
| ----------- | ----------- | ---------------------------------------------------- |
| dev         | eks-dev     | Every merge to main deploys here automatically       |
| staging     | eks-staging | Release candidates, load tests, migration rehearsals |
| production  | eks-prod    | Customer traffic; deploys require an approved release |

Namespaces are per team (`billing`, `identity`, `platform`). Resource quotas
and network policies are applied per namespace.

## CI/CD pipeline

CI runs on GitHub Actions: lint, type check, tests, container build, and an
image scan with Trivy. Images are pushed to ECR tagged with the commit SHA.

CD is GitOps with ArgoCD. The `deploy-manifests` repository is the single
source of truth: a deploy is a pull request that bumps the image tag for an
environment. ArgoCD syncs the cluster to the repository state within two
minutes. Production deploys happen by promoting the exact image digest that
passed staging.

## Rollbacks

A rollback is a `git revert` of the manifest change; ArgoCD converges the
cluster back to the previous state. Database migrations must therefore stay
backward compatible one release in both directions (expand–migrate–contract
pattern).

## Infrastructure as code

All AWS infrastructure (VPC, EKS, RDS, SQS queues, S3 buckets, IAM) is
managed with Terraform in the `infra` repository. Changes go through plan
review in CI; `terraform apply` runs only from the pipeline, never from
laptops.

## Observability

Prometheus scrapes every pod (metrics port `9090`), Grafana dashboards are
provisioned from code, logs ship via Loki, and traces go through
OpenTelemetry to Tempo. Every service exposes `/healthz` (liveness) and
`/readyz` (readiness). Alerts page the owning team through PagerDuty.

## Secrets management

Secrets live in AWS Secrets Manager and are synced into Kubernetes by
external-secrets-operator. Application pods read them as environment
variables. Secrets are never committed to git and never baked into images;
rotation is quarterly, or immediate after any suspected exposure.
