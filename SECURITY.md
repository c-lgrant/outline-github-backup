# Security Policy

This project handles Outline API tokens, GitHub tokens, and webhook signing
secrets, and exposes a public webhook endpoint. Security reports are taken
seriously.

## Supported versions

Only the latest release receives security fixes. There are no maintenance
branches for older versions — upgrade to the newest tag before reporting.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via
[GitHub private vulnerability reporting](https://github.com/c-lgrant/outline-github-backup/security/advisories/new)
("Report a vulnerability" on the repo's Security tab).

Include what you can of:

- affected version / commit
- reproduction steps or a proof of concept
- impact assessment (what an attacker gains)

You should get an initial response within **7 days**. Once a fix ships, the
advisory will be published and credited to you unless you prefer otherwise.

## Scope notes for deployers

- The webhook endpoint authenticates with an HMAC signature
  (`OUTLINE_WEBHOOK_SECRET` is mandatory; the service refuses to start
  without it). Rotate the secret if you suspect exposure.
- The backup mirror inherits the visibility of the destination repo — use a
  **private** data repo unless your Outline content is public.
- Tokens are supplied via environment variables; never bake them into
  images or commit them to compose files in public repos.
