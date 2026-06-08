"""Tests for the selector eval harness."""

from __future__ import annotations

from cloud_engineer_mcp.eval import EvalCase, EvalResult, run_eval
from cloud_engineer_mcp.eval.catalog import CATALOG, catalog_size
from cloud_engineer_mcp.eval.runner import _first_match_rank, load_dataset


class TestDataset:
    def test_dataset_loads(self) -> None:
        cases = load_dataset()
        assert len(cases) >= 30
        assert all(isinstance(c, EvalCase) for c in cases)

    def test_dataset_expected_tools_exist_in_catalog(self) -> None:
        catalog_names = {entry.namespaced_name for entry in CATALOG}
        cases = load_dataset()
        for case in cases:
            for tool_name in case.expected:
                assert tool_name in catalog_names, (
                    f"Eval case {case.query!r} references unknown tool {tool_name!r}; "
                    f"either add it to catalog.py or fix the dataset."
                )


class TestFirstMatchRank:
    def test_first_match(self) -> None:
        from unittest.mock import MagicMock

        results = [
            MagicMock(namespaced_name="a"),
            MagicMock(namespaced_name="b"),
            MagicMock(namespaced_name="c"),
        ]
        assert _first_match_rank(results, ("b",)) == 2
        assert _first_match_rank(results, ("a",)) == 1
        assert _first_match_rank(results, ("c",)) == 3
        assert _first_match_rank(results, ("missing",)) is None

    def test_first_match_with_alternatives(self) -> None:
        from unittest.mock import MagicMock

        results = [
            MagicMock(namespaced_name="x"),
            MagicMock(namespaced_name="b"),
            MagicMock(namespaced_name="c"),
        ]
        # Multiple acceptable — first one in result order wins.
        assert _first_match_rank(results, ("b", "c")) == 2
        assert _first_match_rank(results, ("c", "x")) == 1


class TestRunEvalKeywordMode:
    """Keyword mode doesn't require the embedding model."""

    def test_runs_against_bundled_catalog(self) -> None:
        result = run_eval(use_embeddings=False)
        assert result.total_cases > 0
        assert result.catalog_size == catalog_size()
        assert result.mode == "keyword"
        # Even keyword mode should hit ≥80% on the bundled dataset.
        # If this drops, dataset and catalog have drifted apart.
        assert result.recall_at[15] >= 0.80

    def test_runs_with_custom_case(self) -> None:
        cases = [EvalCase(query="list my S3 buckets", expected=("aws_prod__list_buckets",))]
        result = run_eval(cases=cases, use_embeddings=False)
        assert result.total_cases == 1
        assert result.recall_at[15] == 1.0

    def test_miss_recorded(self) -> None:
        cases = [EvalCase(query="zzz nonsense", expected=("nonexistent_tool",))]
        result = run_eval(cases=cases, use_embeddings=False)
        assert result.total_cases == 1
        assert result.recall_at[15] == 0.0
        assert len(result.misses) == 1


class TestEvalResult:
    def test_passed(self) -> None:
        r = EvalResult(total_cases=10, recall_at={15: 0.9})
        assert r.passed(threshold_at_15=0.85)
        assert not r.passed(threshold_at_15=0.95)

    def test_passed_when_missing_k(self) -> None:
        # No K=15 recorded — treat as 0, so any positive threshold fails.
        r = EvalResult(total_cases=10, recall_at={5: 1.0})
        assert not r.passed(threshold_at_15=0.5)
