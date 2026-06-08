"""Selector evaluation harness.

Measures Recall@K over a labeled eval set so selector changes (model swaps,
context-extractor tweaks, structured-intent weighting) can be validated
quantitatively instead of by gut feel.

CLI: `cloud-engineer-mcp eval`. Importable: `from cloud_engineer_mcp.eval import run_eval`.
"""

from __future__ import annotations

from cloud_engineer_mcp.eval.runner import EvalCase, EvalResult, run_eval

__all__ = ["EvalCase", "EvalResult", "run_eval"]
