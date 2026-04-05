"""Tests for the SessionManager."""

from __future__ import annotations

import time

from cloud_engineer_mcp.session.sessions import Session, SessionManager


class TestSession:
    def test_add_message(self) -> None:
        s = Session(session_id="test")
        s.add_message("user", "hello")
        assert len(s.conversation_messages) == 1
        assert s.conversation_messages[0]["role"] == "user"

    def test_message_history_limit(self) -> None:
        s = Session(session_id="test")
        for i in range(25):
            s.add_message("user", f"msg {i}")
        assert len(s.conversation_messages) == 20

    def test_record_tool_call(self) -> None:
        s = Session(session_id="test")
        s.record_tool_call("aws_s3__list_buckets")
        assert "aws_s3__list_buckets" in s.tool_call_history

    def test_tool_history_limit(self) -> None:
        s = Session(session_id="test")
        for i in range(15):
            s.record_tool_call(f"tool_{i}")
        assert len(s.tool_call_history) == 10

    def test_pinning(self) -> None:
        s = Session(session_id="test")
        s.pin_backend_tools(["aws_s3__a", "aws_s3__b"])
        assert "aws_s3__a" in s.pinned_tools
        assert s.pinned_tools["aws_s3__a"] == 3

    def test_pin_decay(self) -> None:
        s = Session(session_id="test")
        s.pin_backend_tools(["t1"])
        s.decay_pins()
        assert s.pinned_tools["t1"] == 2
        s.decay_pins()
        assert s.pinned_tools["t1"] == 1
        s.decay_pins()
        assert "t1" not in s.pinned_tools

    def test_score_boosts(self) -> None:
        s = Session(session_id="test")
        s.pin_backend_tools(["t1", "t2"])
        boosts = s.get_score_boosts()
        assert boosts["t1"] == 0.3
        assert boosts["t2"] == 0.3

    def test_set_context(self) -> None:
        s = Session(session_id="test")
        s.set_context("Deploy S3 bucket", ["aws"])
        assert s.context == "Deploy S3 bucket"
        assert s.cloud_providers == ["aws"]
        assert len(s.conversation_messages) == 1


class TestSessionManager:
    def test_get_or_create(self) -> None:
        mgr = SessionManager()
        s1 = mgr.get_or_create("sess1")
        s2 = mgr.get_or_create("sess1")
        assert s1 is s2

    def test_creates_new_session(self) -> None:
        mgr = SessionManager()
        s1 = mgr.get_or_create("a")
        s2 = mgr.get_or_create("b")
        assert s1.session_id != s2.session_id

    def test_cleanup_expired(self) -> None:
        mgr = SessionManager(ttl_seconds=0)
        mgr.get_or_create("old_session")
        time.sleep(0.01)
        removed = mgr.cleanup_expired()
        assert removed == 1
        assert mgr.active_count == 0

    def test_active_count(self) -> None:
        mgr = SessionManager()
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        assert mgr.active_count == 2
