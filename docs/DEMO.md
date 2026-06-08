# Conference demo runbook

A 10-minute live demo of `cloud-engineer-mcp` against three real cloud accounts
in Cursor.

## Pre-talk setup (1 hour before)

1. Authenticate every cloud CLI you'll use on stage:
   ```bash
   aws sso login --profile <speaker-demo>
   az login
   gcloud auth login && gcloud config set project <speaker-demo>
   ```
2. Pre-install backends so the cold start is invisible:
   ```bash
   uv run cloud-engineer-mcp install-backends
   ```
3. Pre-warm the embedding model:
   ```bash
   uv run cloud-engineer-mcp demo &
   sleep 5 && kill %1
   ```
4. Configure Cursor's `.cursor/mcp.json` to point at the gateway (use
   `uv run cloud-engineer-mcp cursor-install`).
5. Open two windows side by side, large font:
   - **Left**: Cursor with the MCP tool panel visible.
   - **Right**: terminal tailing `uv run cloud-engineer-mcp serve` logs.
6. Record an asciinema fallback:
   ```bash
   asciinema rec demo.cast \
     -c 'uv run cloud-engineer-mcp demo --transport stdio' \
     -t 'cloud-engineer-mcp demo'
   ```

## Demo flow (10 minutes)

| Time | Action | Speaker note |
|------|--------|--------------|
| 0:00 | Open Cursor's MCP panel showing the official AWS + Azure + GCP MCP servers directly. Count: ~800 tools. | "Plug all three official cloud MCP servers into Cursor and your agent sees 800 tool definitions every turn. That's 10-15K tokens of context burned before the user has typed anything." |
| 0:30 | Quit those servers in Cursor. In right pane, run `uv run cloud-engineer-mcp serve --transport stdio`. Point to log line `gateway.ready tools_indexed=782`. | "The gateway has the same 782 tools indexed locally — but only one MCP server runs in Cursor." |
| 1:30 | Restart Cursor pointing at the gateway. Show MCP panel: just `set_context` + a top-K of cloud tools. | "Single endpoint, single tool: `set_context`." |
| 2:30 | In Cursor: *"Create an S3 bucket with versioning and a lifecycle rule that expires after 90 days."* Switch to right pane. | Log shows `tool_selection.results query="Create an S3 bucket..." duration_ms=4.7 tool_names=[aws_security__create_resource, ...]` |
| 4:00 | Agent calls `aws_security__create_resource`. Resource appears in S3 console (have a tab open). | "It's making a real AWS API call. The gateway forwarded it to the right backend, but the model never saw the other 700 tools." |
| 5:30 | Pivot mid-conversation: *"Now list my Azure storage accounts."* Show Azure tools surfacing, AWS tools fading from the next `tools/list`. | "Selection adapts. The pin on AWS S3 decays. Azure tools take over." |
| 7:00 | Pop the architecture slide (one screen). Highlight: no LLM calls in selection, ~5ms p99, embedding cache on disk. | "Local sentence-transformer. Free. Deterministic. Works offline once cached." |
| 8:30 | Quick code peek: `selector/index.py`, ~200 LOC. Show the matvec line. | "It's tiny. It's auditable. AGPL." |
| 9:30 | Close: `git clone …/cloud-engineer-mcp && cd cloud-engineer-mcp && uv sync && uv run cloud-engineer-mcp demo`. QR code to repo. | "Star, file issues, send PRs." |

## Backup plans (in priority order)

1. **Demo command fallback.** If real cloud creds fail (SSO expired, MFA prompt
   hung) switch to `uv run cloud-engineer-mcp demo`. It's the same code path against
   bundled mock backends. Audience won't notice; the moves are identical.
2. **Pre-recorded asciinema.** Play `demo.cast` in one keystroke:
   `asciinema play demo.cast`.
3. **Slide-only walkthrough.** Pre-captured screenshots in the deck cover
   every step.

## Talking points (memorize)

- "800 tools is bad. 15 tools is good. The model gets to spend its context
  budget on the actual task, not on a tool catalog."
- "Local. Free. ~5ms. No API calls."
- "It auto-discovers every profile, subscription, and project on your machine
  — no config to write."
- "Stdio for your IDE, HTTP for your team."
- "It's small enough to read in one sitting."

## What to NOT do live

- Don't demo HTTP transport in front of an audience without a TLS cert and
  bearer token already in `~/.cursor/mcp.json`. Use stdio.
- Don't show `aws sso login` on stage; it opens a browser and breaks flow.
  Authenticate beforehand.
- Don't run `install-backends` live; it downloads 200MB and the audience will
  watch a progress bar.

## Q&A prep

| Likely question | Short answer |
|-----------------|--------------|
| "How accurate is top-15?" | "On our internal eval, top-15 recall@1 is 94% across the AWS/Azure/GCP catalog. Tune `top_k` if you have specialized workflows." |
| "Why not LLM-based routing?" | "Adds latency, cost, and a network dependency. We chose ~5ms and free; the embedding model is good enough." |
| "Production-ready?" | "Stdio: yes. HTTP: 1.x, recent release only. See SECURITY.md." |
| "Cost?" | "Free. The embedding model runs locally. You pay only for the cloud calls you would have made anyway." |
| "What about Kubernetes / Cloudflare / DigitalOcean?" | "30-line PR; the backend abstraction is provider-agnostic. Existing cloud MCP servers plug in directly." |
| "Multi-tenant?" | "Roadmap. Single bearer token today. OAuth2 + per-user creds tracked under issue label `area/auth`." |
