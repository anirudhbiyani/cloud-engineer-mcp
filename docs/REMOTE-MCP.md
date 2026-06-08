# Remote MCP servers

`cloud-engineer-mcp` supports two transports per backend:

- **stdio**: gateway spawns an MCP server as a subprocess (default).
- **http**: gateway connects to a remote streamable-HTTP MCP server. No
  subprocess.

This document covers the http path: when to use it, what's bundled, and how
to authenticate.

## When you'd want http

- **Google Cloud's managed MCP servers** (~40 endpoints under
  `*.googleapis.com/mcp`). Designed to be reached as a service; you'd never
  spawn one locally.
- **GitHub MCP** (`https://api.githubcopilot.com/mcp`). GitHub hosts the
  server; you supply a PAT.
- **Microsoft Learn MCP** (`https://learn.microsoft.com/api/mcp`). Anonymous.
- **AWS Knowledge MCP** (`https://knowledge-mcp.global.api.aws`). Anonymous.
- **Your own hosted MCP behind an internal URL.** Anything that speaks the
  MCP streamable-HTTP transport.

## What's bundled

Built-in remote integrations are under `discovery.*` in `config.yml`:

```yaml
discovery:
  github_remote:
    enabled: true
    token_env: GITHUB_TOKEN
  microsoft_learn:
    enabled: true
  aws_knowledge:
    enabled: true
  gcp_remote:
    enabled: true
    services:
      - name: bigquery
        url: https://bigquery.googleapis.com/mcp
      - name: gke
        url: https://container.googleapis.com/mcp
      - name: storage
        url: https://storage.googleapis.com/storage/mcp
```

The full list of Google managed-MCP endpoints lives at
<https://docs.cloud.google.com/mcp/supported-products>. Paste any of them as
`{ name, url }` pairs into `gcp_remote.services`.

## Authentication

Headers support `${ENV_VAR}` interpolation, so secrets stay out of files.

### Bearer token from an env var (GitHub, generic)

```yaml
backends:
  my_internal_mcp:
    display_name: "Internal MCP"
    transport: http
    url: https://mcp.internal.corp/
    headers:
      Authorization: "Bearer ${INTERNAL_MCP_TOKEN}"
```

### GCP managed MCP (token from `gcloud`)

The gateway calls `gcloud auth print-access-token` at startup and stamps the
result as a Bearer header into every `gcp_remote` service. Override the
command if you use a service account or workload identity::

```yaml
discovery:
  gcp_remote:
    enabled: true
    token_command:
      - gcloud
      - auth
      - "--impersonate-service-account=runner@my-proj.iam.gserviceaccount.com"
      - print-access-token
    services:
      - name: bigquery
        url: https://bigquery.googleapis.com/mcp
```

Or use a fixed token from env::

```yaml
discovery:
  gcp_remote:
    enabled: true
    token_env: GCP_ACCESS_TOKEN          # gateway reads ${GCP_ACCESS_TOKEN}
    services: [...]
```

### Anonymous (Microsoft Learn, AWS Knowledge)

No auth. Enable and go.

## Token lifetime

GCP access tokens from `gcloud auth print-access-token` are ~1 hour. The
gateway acquires one token at discovery and uses it for the lifetime of the
gateway process. For long-running deployments:

- Restart the gateway hourly via systemd/Kubernetes.
- Or set `token_env` to a long-lived token from a service-account key.
- Token rotation without restart is a roadmap item — see issues labeled
  `area/auth`.

## Configuring a manual remote backend

Skip discovery entirely; specify everything under the top-level `backends:`
block:

```yaml
backends:
  bigquery_remote:
    display_name: "BigQuery (managed MCP)"
    transport: http
    url: https://bigquery.googleapis.com/mcp
    headers:
      Authorization: "Bearer ${GCP_ACCESS_TOKEN}"
    enabled: true
    startup_timeout_seconds: 30
```

This works for any MCP-compatible HTTPS endpoint.

## Mixing stdio and http freely

The gateway treats both as equal first-class backends. The selector ranks
their tools together; the policy engine guards them identically; the
metrics endpoint reports them side by side. The only difference shows up in
logs (`backend.transport=stdio|http`) and in `cloud-engineer-mcp discover`.

## Caveats

- Remote MCP servers cannot stream stdout/stderr the way subprocesses do.
  Backend errors show up only as MCP-level error responses.
- Some remote endpoints rate-limit per-token. Combine with `policy.rate_limits`
  to spread load.
- `health_check_interval_seconds` defaults to 120s for remote backends (twice
  the stdio default) because each check costs an HTTPS round trip.
