# Security Policy

The Planificateur de Gardes is used by medical clinics: we take security reports seriously.

## Reporting a vulnerability

Please report vulnerabilities **privately** by e-mail to **security@arrakis.lu**.
Do not open a public GitHub issue, discussion or pull request for a security problem.

Please include, when possible:

- the affected version or commit, and the component (URL, endpoint, file);
- the steps to reproduce, a proof of concept and the impact you observed;
- whether the issue is already public or known to others.

Never access, modify or keep data that is not yours: test with your own local instance
(`docker compose up`), not with a production deployment.

## Our commitments

- **Acknowledgement within 72 hours** of your report.
- An initial assessment and, when confirmed, a fix plan as soon as possible.
- **Coordinated disclosure within 30 days**: we publish the fix and an advisory, and credit you
  if you wish. If more time is needed, we agree on it with you.

## PGP

PGP key: *not published yet* (placeholder). Until then, ask for an encrypted channel in a first
message without sensitive details.

## Supported versions

Only the latest release receives security fixes.

## Automated checks

The repository uses GitHub **Code Scanning (CodeQL)**, **Dependabot** (Python, GitHub Actions,
Docker) and **Secret Scanning**. Each release is also checked with `pip-audit` and Trivy.
