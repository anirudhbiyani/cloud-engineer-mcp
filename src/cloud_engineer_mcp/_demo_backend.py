"""Bundled mock MCP backend used by `cloud-engineer-mcp demo`.

A self-contained, no-cloud-credentials backend that exposes a handful of
realistic-looking AWS/Azure/GCP-style tools so first-time users can experience
the gateway end-to-end in under a minute.

Runs as a subprocess (just like real backends) via
`python -m cloud_engineer_mcp._demo_backend`.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cloud-engineer-mcp-demo")


@mcp.tool()
def list_s3_buckets() -> list[dict[str, object]]:
    """List all S3 buckets in the demo AWS account."""
    return [
        {"name": "demo-logs", "region": "us-east-1", "created": "2025-01-12"},
        {"name": "demo-artifacts", "region": "us-east-1", "created": "2025-02-03"},
    ]


@mcp.tool()
def create_s3_bucket(
    name: str, region: str = "us-east-1", versioning: bool = False
) -> dict[str, object]:
    """Create an S3 bucket with optional versioning."""
    return {"name": name, "region": region, "versioning": versioning, "status": "created"}


@mcp.tool()
def list_lambda_functions() -> list[dict[str, object]]:
    """List Lambda functions in the demo AWS account."""
    return [
        {"name": "image-resizer", "runtime": "python3.12", "memory_mb": 512},
        {"name": "webhook-router", "runtime": "nodejs20.x", "memory_mb": 256},
    ]


@mcp.tool()
def list_azure_storage_accounts() -> list[dict[str, object]]:
    """List Azure storage accounts in the demo subscription."""
    return [
        {"name": "demoacct01", "tier": "Standard_LRS", "kind": "StorageV2"},
    ]


@mcp.tool()
def list_azure_vms() -> list[dict[str, object]]:
    """List Azure virtual machines in the demo subscription."""
    return [
        {"name": "demo-web-01", "size": "Standard_B2s", "os": "Ubuntu 22.04"},
    ]


@mcp.tool()
def list_gcp_buckets() -> list[dict[str, object]]:
    """List Google Cloud Storage buckets in the demo project."""
    return [
        {"name": "demo-gcs-public", "location": "US-CENTRAL1", "storage_class": "STANDARD"},
    ]


@mcp.tool()
def list_gke_clusters() -> list[dict[str, object]]:
    """List Google Kubernetes Engine clusters in the demo project."""
    return [
        {"name": "demo-cluster-prod", "location": "us-central1", "node_count": 3},
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
