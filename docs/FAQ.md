# FAQ

### Why not just use the official AWS / Azure / GCP MCP servers directly?

You can — and if you only need one cloud, you should. `cloud-engineer-mcp` is
for the case where an agent benefits from access to all three and you'd rather
not pay 10–15K context tokens per turn for tool definitions you'll never use
this conversation.

### How is this different from a router or a registry?

A router picks one backend; the gateway always speaks to all of them. A
registry serves tool metadata; we serve a **filtered, ranked** subset chosen
by local embeddings against the current conversation context.

### Does it call any external service?

No. The embedding model is `all-MiniLM-L6-v2`, ~80MB, runs locally on CPU. No
API keys. The only network calls the gateway itself makes are to the cloud
CLIs (`aws sts get-caller-identity`, `az account list`, `gcloud projects
list`) during the discovery phase.

### How much does it cost to run?

The gateway is free. Downstream cost is whatever your cloud account would have
charged anyway when tools are called. No per-token, per-call, or per-tool
overhead.

### Will my agent miss tools it actually needs?

Possible. Two mitigations are built in:

1. **`set_context`** — calling it with a richer description (or with
   `cloud_providers=["aws"]`) generally tightens selection.
2. **Backend pinning** — when the agent calls a tool from backend B, the
   other tools in B get a score boost for the next few `tools/list` calls so
   coherent workflows stay coherent.

Increase `selector.top_k` (default 15) if you regularly see the agent miss
relevant tools. The embedding overhead is the same; you only pay extra context
in the model.

### Why a sentence-transformer model and not BM25 or LLM-based selection?

BM25 is the fallback path for when the model can't load. It works but loses
recall on natural-language paraphrase (e.g. "list my storage" vs "list S3
buckets"). LLM-based selection would work better but adds latency, cost, and a
network dependency. The chosen middle ground is ~5ms and free.

### Can I use a different embedding model?

Yes — set `selector.model_name` to any
[`sentence-transformers`](https://www.sbert.net/) model. Trade-offs: bigger
models cost more memory but rarely improve top-K for our domain. Stick with
the default unless you have a specific reason.

### Does the embedding cache need to be invalidated when I add a backend?

It's automatic. The cache is invalidated when the tool-name list changes
(different set or different order). You'll see `index.cache_invalidated` in
the logs and the next build will re-embed everything (~3s for 800 tools).

### Why AGPL?

To keep the project an open commons. AGPL applies only if you provide
`cloud-engineer-mcp` itself as a network service to third parties. Internal
use, embedding in your agent stack, and using it locally are all fine. See
the README's "Why AGPL" section.

### Can I run it in production?

Yes for the stdio transport (IDE integration). For the HTTP transport, make
sure you're on a recent release (the HTTP transport was broken in 1.0.0 and
fixed in `[Unreleased]`), set `CLOUD_ENGINEER_MCP_AUTH_TOKEN`, put it behind
TLS, and follow the checklist in [SECURITY.md](../SECURITY.md).

### Multi-tenant?

Not yet. Single bearer token; one set of cloud creds per gateway. Multi-tenant
auth (OAuth2, per-user creds) is a roadmap item — see the issues labeled
`area/auth`.

### Why doesn't it support [my cloud]?

Most likely nobody has gotten to it yet. Backend support is provider-agnostic
— add a 30-line `discover_<provider>()` and a `BackendConfig`-producing arm
in `expand_backends()`. PRs welcome.

### How do I report a bug?

Use the [bug report template](https://github.com/cloud-engineer-mcp/cloud-engineer-mcp/issues/new?template=bug_report.yml).
Include the output of `cloud-engineer-mcp check` and a redacted snippet of
your config.

### How do I report a security issue?

Privately, per [SECURITY.md](../SECURITY.md). Not in the public issue tracker.
