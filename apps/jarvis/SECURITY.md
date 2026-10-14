# Security policy

## Supported versions

Security fixes are currently handled on the default development branch. No release support window is published yet.

## Reporting a vulnerability

Do not post exploitable details, credentials, or personal data in a public issue. A private reporting channel has not been confirmed for this repository. Before making the repository public, the owner should enable GitHub private vulnerability reporting or publish another private contact method.

Include the affected component, impact, and steps to reproduce. Do not include live credentials or private user data in the report; use a synthetic example instead.

## Secret handling

- Keep credentials in environment variables or a local secret store; never commit them.
- Keep `.env`, `config.local.yaml`, personal vaults, raw provider transcripts, and generated run artifacts out of Git.
- Rotate any credential that may have been committed, even if it was later removed from the current file tree. Removing a value from the latest revision does not remove it from Git history.
- Treat integrations with local services, external providers, and deployment scripts as environment-specific; review their permissions and configuration before use.
