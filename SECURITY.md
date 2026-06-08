# Security Policy

## Supported versions

We patch security issues in the latest minor version. During the beta period
(0.x) and 1.x, only the most recent release receives security fixes.

| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |
| < 1.0   | ❌        |

## Threat model

`cloud-engineer-mcp` runs as a **trusted local process** with delegated
credentials to your cloud accounts (AWS profiles, Azure subscriptions, GCP
projects). Compromise of the gateway is equivalent to compromise of those
accounts.

Defaults aim to keep the gateway local:

- HTTP transport binds to `127.0.0.1` by default.
- The gateway **refuses to start** if HTTP is bound to a non-loopback host and
  `CLOUD_ENGINEER_MCP_AUTH_TOKEN` is not set.
- CORS is **empty** by default — no browser origin can reach `/mcp` unless you
  explicitly list it.
- Backend subprocesses get only the env vars listed in `BackendConfig.env`
  (plus the gateway's own env). Cloud credentials come from mounted CLI config
  files (`~/.aws`, `~/.azure`, `~/.config/gcloud`).
- Config dumps (`/health`, log lines that include redacted config) mask values
  for env vars whose name contains `SECRET`, `KEY`, `TOKEN`, `PASSWORD`, or
  `CREDENTIAL`.

What is **not** in the threat model today:

- Multi-tenant deployments where untrusted users share one gateway. The bearer
  token gate is intentionally a single-token model; per-user authentication is
  on the roadmap.
- Sandboxing of backend subprocesses. Backends inherit the gateway process's
  filesystem access.
- Defending against a malicious backend MCP server. The gateway trusts tool
  definitions and tool-call results from the backends it spawns.

## Production deployment checklist

- [ ] `CLOUD_ENGINEER_MCP_AUTH_TOKEN` set to a high-entropy secret.
- [ ] HTTP transport behind TLS (reverse proxy or service mesh).
- [ ] Network ACL restricting `/mcp` to known client IPs.
- [ ] Cloud credentials mounted with least-privilege IAM (per-profile/sub/project).
- [ ] Container or VM runs as a non-root user.
- [ ] Logs shipped to a SIEM; alerts on `tool_call.error` rate.
- [ ] Rate limit appropriately tuned for your client count.

## Reporting a vulnerability

Please report security issues **privately**.

- **Email:** `security@cloud-engineer-mcp.dev` (PGP key on request).
- **GitHub:** Use [private vulnerability reporting](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/security/advisories/new).

We aim to acknowledge within 2 business days, agree on a disclosure timeline
within 7 days, and ship a fix within 30 days for critical issues. We're happy
to credit reporters in the release notes if you'd like.

Please do **not**:

- Open a public GitHub issue for security-sensitive problems.
- Test for vulnerabilities against cloud-engineer-mcp deployments you don't own.
- Publicly disclose details before we've shipped a fix.
