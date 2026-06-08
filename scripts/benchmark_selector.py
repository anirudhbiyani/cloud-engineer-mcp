#!/usr/bin/env python3
"""Benchmark the embedding-based tool selector performance."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cloud_engineer_mcp.backends.registry import ToolRef
from cloud_engineer_mcp.selector.engine import EmbeddingEngine
from cloud_engineer_mcp.selector.index import ToolIndex


def generate_fake_refs(n: int) -> list[ToolRef]:
    """Generate n fake ToolRefs with realistic descriptions."""
    from unittest.mock import MagicMock

    providers = ["aws", "azure", "gcp"]
    services = [
        "s3",
        "ec2",
        "lambda",
        "dynamodb",
        "rds",
        "sqs",
        "sns",
        "storage",
        "compute",
        "functions",
        "cosmos",
        "sql",
        "gcs",
        "bigquery",
        "gke",
        "pubsub",
        "dataflow",
    ]
    actions = [
        "create",
        "delete",
        "list",
        "get",
        "update",
        "describe",
        "start",
        "stop",
        "configure",
        "deploy",
        "monitor",
    ]

    refs = []
    for i in range(n):
        provider = providers[i % len(providers)]
        service = services[i % len(services)]
        action = actions[i % len(actions)]
        name = f"{action}_{service}"
        backend_id = f"{provider}_{service}"
        desc = f"{action.title()} a {service} resource in {provider.upper()}"

        tool = MagicMock()
        tool.name = name
        tool.description = desc

        refs.append(
            ToolRef(
                namespaced_name=f"{backend_id}__{name}",
                original_name=name,
                backend_id=backend_id,
                tool=tool,
                description_for_embedding=f"[{provider.upper()}] {name}: {desc}",
            )
        )
    return refs


def main() -> None:
    print("Loading embedding model...")
    engine = EmbeddingEngine("all-MiniLM-L6-v2")
    engine._load_sync()
    print(f"Model loaded (dim={engine.dimension})")

    for n_tools in [50, 100, 200, 500]:
        print(f"\n--- {n_tools} tools ---")
        refs = generate_fake_refs(n_tools)

        t0 = time.perf_counter()
        index = ToolIndex(engine, min_similarity=0.1)
        index.build(refs)
        build_time = (time.perf_counter() - t0) * 1000
        print(f"Index build: {build_time:.1f}ms")

        queries = [
            "Create an S3 bucket with versioning",
            "Deploy a Lambda function",
            "List Azure virtual machines",
            "Query BigQuery dataset",
            "Monitor EC2 instances",
        ]

        search_times = []
        for query in queries:
            t0 = time.perf_counter()
            index.search(query, top_k=15)
            elapsed = (time.perf_counter() - t0) * 1000
            search_times.append(elapsed)

        avg = np.mean(search_times)
        p99 = np.percentile(search_times, 99)
        print(f"Search avg: {avg:.2f}ms, p99: {p99:.2f}ms")

        if avg > 50:
            print("WARNING: Search exceeds 50ms target!")
        else:
            print("PASS: Under 50ms target")


if __name__ == "__main__":
    main()
