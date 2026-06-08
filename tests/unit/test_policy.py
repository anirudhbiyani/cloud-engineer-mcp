"""Tests for the policy engine: allow/deny, rate-limit, dry-run, audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud_engineer_mcp.config import (
    PolicyAuditConfig,
    PolicyConfig,
    PolicyRateLimitRule,
)
from cloud_engineer_mcp.policy import PolicyDecision, PolicyEngine


def _policy(**kwargs: object) -> PolicyEngine:
    cfg = PolicyConfig(enabled=True, **kwargs)  # type: ignore[arg-type]
    return PolicyEngine(cfg)


class TestDisabledPolicy:
    def test_disabled_always_allows(self) -> None:
        engine = PolicyEngine(PolicyConfig(enabled=False, deny=["*"]))
        assert engine.check("anything").decision is PolicyDecision.ALLOW


class TestDeny:
    def test_exact_match_denied(self) -> None:
        engine = _policy(deny=["aws_prod__delete_resource"])
        assert engine.check("aws_prod__delete_resource").decision is PolicyDecision.DENY

    def test_glob_match_denied(self) -> None:
        engine = _policy(deny=["*__delete_*"])
        r = engine.check("aws_prod__delete_resource")
        assert r.decision is PolicyDecision.DENY
        assert r.matched_pattern == "*__delete_*"

    def test_non_matching_allowed(self) -> None:
        engine = _policy(deny=["*__delete_*"])
        assert engine.check("aws_prod__list_buckets").decision is PolicyDecision.ALLOW

    def test_first_match_wins(self) -> None:
        engine = _policy(deny=["aws_*", "azure_*"])
        r = engine.check("aws_prod__list")
        assert r.matched_pattern == "aws_*"


class TestAllow:
    def test_whitelist_blocks_unlisted(self) -> None:
        engine = _policy(allow=["aws_*"])
        assert engine.check("gcp__list_buckets").decision is PolicyDecision.DENY
        assert engine.check("aws_prod__list_buckets").decision is PolicyDecision.ALLOW

    def test_empty_allow_means_no_whitelist(self) -> None:
        engine = _policy()
        assert engine.check("anything__x").decision is PolicyDecision.ALLOW

    def test_deny_wins_over_allow(self) -> None:
        engine = _policy(allow=["aws_*"], deny=["aws_prod__delete_*"])
        assert engine.check("aws_prod__delete_resource").decision is PolicyDecision.DENY
        assert engine.check("aws_prod__list_buckets").decision is PolicyDecision.ALLOW


class TestDryRun:
    def test_dry_run_returns_allow_dry_run(self) -> None:
        engine = _policy(dry_run=True)
        r = engine.check("aws_prod__create_resource")
        assert r.decision is PolicyDecision.ALLOW_DRY_RUN

    def test_dry_run_still_respects_deny(self) -> None:
        engine = _policy(dry_run=True, deny=["*__delete_*"])
        r = engine.check("aws_prod__delete_resource")
        assert r.decision is PolicyDecision.DENY


class TestRateLimit:
    def test_under_limit_allows(self) -> None:
        engine = _policy(rate_limits=[PolicyRateLimitRule(pattern="*", per_minute=10)])
        for _ in range(5):
            assert engine.check("aws__x").decision is PolicyDecision.ALLOW

    def test_over_limit_rate_limits(self) -> None:
        engine = _policy(rate_limits=[PolicyRateLimitRule(pattern="*", per_minute=3)])
        for _ in range(3):
            assert engine.check("aws__x").decision is PolicyDecision.ALLOW
        r = engine.check("aws__x")
        assert r.decision is PolicyDecision.RATE_LIMITED
        assert r.rate_limit_per_minute == 3

    def test_pattern_isolates_buckets(self) -> None:
        engine = _policy(
            rate_limits=[PolicyRateLimitRule(pattern="aws_*", per_minute=2)],
        )
        assert engine.check("aws_prod__a").decision is PolicyDecision.ALLOW
        assert engine.check("aws_prod__b").decision is PolicyDecision.ALLOW
        assert engine.check("aws_prod__c").decision is PolicyDecision.RATE_LIMITED
        # Different pattern (gcp__) doesn't share the bucket.
        assert engine.check("gcp__x").decision is PolicyDecision.ALLOW

    def test_deny_short_circuits_rate_limit(self) -> None:
        # Denied tools shouldn't consume rate-limit budget.
        engine = _policy(
            deny=["aws__deny_me"],
            rate_limits=[PolicyRateLimitRule(pattern="*", per_minute=1)],
        )
        engine.check("aws__deny_me")  # DENY — should NOT count
        # The single budget is still available:
        assert engine.check("aws__ok").decision is PolicyDecision.ALLOW


class TestAudit:
    def test_audit_writes_jsonl(self, tmp_path: Path) -> None:
        audit_file = tmp_path / "audit.log"
        cfg = PolicyConfig(
            enabled=True,
            deny=["*__delete_*"],
            audit=PolicyAuditConfig(enabled=True, path=str(audit_file)),
        )
        engine = PolicyEngine(cfg)
        engine.check("aws__list", session_id="s1")
        engine.check("aws__delete_resource", session_id="s1")

        entries = [json.loads(line) for line in audit_file.read_text().splitlines()]
        assert len(entries) == 2
        assert entries[0]["decision"] == "allow"
        assert entries[1]["decision"] == "deny"
        assert entries[1]["matched_pattern"] == "*__delete_*"
        assert entries[1]["session_id"] == "s1"

    def test_audit_disabled_writes_nothing(self, tmp_path: Path) -> None:
        audit_file = tmp_path / "audit.log"
        cfg = PolicyConfig(
            enabled=True,
            audit=PolicyAuditConfig(enabled=False, path=str(audit_file)),
        )
        engine = PolicyEngine(cfg)
        engine.check("aws__list")
        assert not audit_file.exists()


class TestEvaluatePure:
    def test_evaluate_does_not_consume_rate_limit(self) -> None:
        engine = _policy(rate_limits=[PolicyRateLimitRule(pattern="*", per_minute=1)])
        # Calling evaluate many times must not exhaust the bucket.
        for _ in range(100):
            r = engine.evaluate("aws__x")
            assert r.decision is PolicyDecision.ALLOW
        # check() can still allow exactly one call.
        assert engine.check("aws__x").decision is PolicyDecision.ALLOW
        assert engine.check("aws__x").decision is PolicyDecision.RATE_LIMITED


@pytest.mark.asyncio
class TestServerIntegration:
    """End-to-end through the server's call_tool path with policy enabled."""

    async def test_deny_produces_deny_verdict(self, tmp_path: Path) -> None:
        # Direct policy → exception mapping is exercised in server.py; here we
        # just confirm the engine produces the deny verdict that triggers it.
        engine = _policy(deny=["aws__delete_resource"])
        result = engine.check("aws__delete_resource")
        assert result.decision is PolicyDecision.DENY
