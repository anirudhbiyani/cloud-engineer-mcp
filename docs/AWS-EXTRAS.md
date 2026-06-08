# AWS extras catalog

The default AWS backend is `awslabs.aws-iac-mcp-server@latest` — broad coverage
of IaC, Cloud Control, CloudFormation, CDK. For workloads that need deeper
service-specific tools, AWS Labs publishes ~30 specialized MCP servers. List
the ones you want under `discovery.aws.extra_servers` in `config.yml` and the
gateway will spawn one subprocess per (profile × extra) on startup.

```yaml
discovery:
  aws:
    enabled: true
    mcp_server: "awslabs.aws-iac-mcp-server@latest"
    extra_servers:
      - "awslabs.lambda-tool-mcp-server@latest"
      - "awslabs.dynamodb-mcp-server@latest"
      - "awslabs.aws-documentation-mcp-server@latest"
```

Each extra increases startup time (a `uvx` cold start is ~1–3s; warm starts
after `cloud-engineer-mcp install-backends` are sub-second) and resident
memory (~30–80MB per Python subprocess). Pick the ones you actually use.

## Catalog (as of 2026)

### Infrastructure & deployment
| Package | What it adds |
|---|---|
| `awslabs.aws-iac-mcp-server` *(default primary)* | CloudFormation + CDK + Cloud Control |
| `awslabs.eks-mcp-server` | EKS clusters |
| `awslabs.ecs-mcp-server` | ECS clusters & services |
| `awslabs.finch-mcp-server` | Local container builds + ECR push |
| `awslabs.aws-serverless-mcp-server` | SAM CLI, serverless apps |
| `awslabs.lambda-tool-mcp-server` | Direct Lambda invocation |
| `awslabs.aws-transform-mcp-server` | AWS Transform workspaces |
| `awslabs.aws-support-mcp-server` | AWS Support cases |

### AI & machine learning
| Package | What it adds |
|---|---|
| `awslabs.bedrock-kb-retrieval-mcp-server` | Bedrock Knowledge Bases |
| `awslabs.amazon-kendra-index-mcp-server` | Kendra enterprise search |
| `awslabs.amazon-qbusiness-anonymous-mcp-server` | Amazon Q Business |
| `awslabs.amazon-qindex-mcp-server` | Amazon Q Index search |
| `awslabs.aws-bedrock-custom-model-import-mcp-server` | Bedrock custom model import |
| `awslabs.amazon-bedrock-agentcore-mcp-server` | Bedrock AgentCore |
| `awslabs.amazon-translate-mcp-server` | Translate API |
| `awslabs.sagemaker-ai-mcp-server` | SageMaker resources |

### Databases & analytics
| Package | What it adds |
|---|---|
| `awslabs.dynamodb-mcp-server` | DynamoDB |
| `awslabs.postgres-mcp-server` | Aurora PostgreSQL (RDS Data API) |
| `awslabs.mysql-mcp-server` | Aurora MySQL (RDS Data API) |
| `awslabs.aurora-dsql-mcp-server` | Aurora DSQL distributed SQL |
| `awslabs.documentdb-mcp-server` | DocumentDB (MongoDB-compatible) |
| `awslabs.amazon-neptune-mcp-server` | Neptune graph database |
| `awslabs.amazon-keyspaces-mcp-server` | Keyspaces (Cassandra) |
| `awslabs.timestream-for-influxdb-mcp-server` | Timestream / InfluxDB |
| `awslabs.s3-tables-mcp-server` | S3 Tables |
| `awslabs.redshift-mcp-server` | Redshift |
| `awslabs.aws-iot-sitewise-mcp-server` | IoT SiteWise |

### Backends, caches, search
| Package | What it adds |
|---|---|
| `awslabs.aws-appsync-mcp-server` | AppSync GraphQL |
| `awslabs.elasticache-mcp-server` | ElastiCache control plane |
| `awslabs.valkey-mcp-server` | ElastiCache/MemoryDB for Valkey |
| `awslabs.memcached-mcp-server` | ElastiCache for Memcached |

### Documentation
| Package | What it adds |
|---|---|
| `awslabs.aws-documentation-mcp-server` | AWS docs + API references (local) |

## Pre-installing for fast startup

If you list ≥3 extras, run `cloud-engineer-mcp install-backends` once. The
installer downloads each `uvx` package into `~/.cloud-engineer-mcp/backends/`
so subsequent gateway starts skip the network entirely.

## Tagging

Backend IDs for extras follow `aws_<profile>_<tag>` where `<tag>` is derived
from the server name (`awslabs.lambda-tool-mcp-server` → `lambda`). The tag
appears in logs, in the `/metrics` endpoint, and in `list-tools` output, so
you can attribute calls back to the originating server.

## Latest list

Source of truth: <https://github.com/awslabs/mcp>. If a server moves or new
ones appear, paste its `awslabs.*-mcp-server@latest` name into the config —
no code change is needed.
