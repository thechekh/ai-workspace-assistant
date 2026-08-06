# Onboarding — Getting Started

Welcome! This guide covers your first week.

## Accounts you need

Your manager files the access request before you start; verify on day one
that you have: GitHub org membership, AWS SSO (dev account by default), Jira
and Confluence, Slack, and PagerDuty (you join your team's rotation after
month one).

## Local setup

1. Install Docker Desktop, uv, and Node 22.
2. Clone the service you'll work on and run `uv sync`.
3. `docker compose up -d` starts local infrastructure (Postgres, Redis,
   Qdrant, LocalStack for SQS and S3).
4. `uv run pytest` must pass before your first change — if it doesn't, ask in
   #eng-help.

## Where things live

- Service code: one repository per service in the GitHub org.
- Deploy manifests: the `deploy-manifests` repository (GitOps, synced by
  ArgoCD).
- Infrastructure: the `infra` repository (Terraform).
- Documentation: Confluence for product docs; architecture docs live next to
  the code in `docs/` folders and are indexed by the AI Workspace Assistant.

## Getting help

Ask questions in #eng-help — no question is too small. Each team also has its
own channel (#team-billing, #team-identity, #team-platform). Your onboarding
buddy is your first point of contact for the first month.

## Your first change

Pick a `good-first-issue` from your team's board, follow the coding
standards, open a small PR, and deploy it to dev after merge. Shipping in
week one is normal and encouraged.
