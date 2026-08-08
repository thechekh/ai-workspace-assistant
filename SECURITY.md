# Security

## Reporting a vulnerability

Open a private security advisory on the repository, or contact the
maintainer directly. Please do not open a public issue for an exploitable
problem.

## Scope and posture

This is an internal/local-network tool. The full threat model — what is
enforced, where each control lives in the code, and what is **deliberately
not built** — is documented in
[docs/reference/security.md](docs/reference/security.md).

Summary: the model's output and any uploaded document are treated as
untrusted; tools are allowlisted and read-only; execution is server-side.
Perimeter controls a production deployment would add (SSO, rate limiting,
egress allowlisting, content sanitisation) are listed there as gaps rather
than implied to exist.

Dependencies are audited on every push and weekly by
[`.github/workflows/security.yml`](.github/workflows/security.yml)
(`pip-audit`, `npm audit`, and CodeQL where GitHub Advanced Security is
available).
