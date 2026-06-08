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


class TestSessionPersistence:
    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        persist = tmp_path / "sessions.json"
        mgr1 = SessionManager(persist_path=str(persist))
        s = mgr1.get_or_create("conf-demo")
        s.set_context("create an S3 bucket", ["aws"], action="create", resource_type="s3 bucket")
        s.record_tool_call("aws__create_resource")
        s.pin_backend_tools(["aws__create_resource", "aws__describe_resource"])
        mgr1._save_to_disk()

        mgr2 = SessionManager(persist_path=str(persist))
        loaded = mgr2.get_or_create("conf-demo")
        assert loaded.context == "create an S3 bucket"
        assert loaded.cloud_providers == ["aws"]
        assert loaded.action == "create"
        assert loaded.resource_type == "s3 bucket"
        assert "aws__create_resource" in loaded.tool_call_history
        assert loaded.pinned_tools == {
            "aws__create_resource": 3,
            "aws__describe_resource": 3,
        }

    def test_expired_sessions_dropped_on_load(self, tmp_path) -> None:
        import time as time_mod

        persist = tmp_path / "sessions.json"
        # Write a session with an ancient last_active timestamp.
        ancient = time_mod.time() - 10_000
        persist.write_text(
            '{"old": {"session_id": "old", "created_at": 0, "last_active": '
            + str(ancient)
            + ', "conversation_messages": [], "tool_call_history": [], '
            + '"pinned_tools": {}, "context": null, "cloud_providers": null, '
            + '"action": null, "resource_type": null}}'
        )
        mgr = SessionManager(ttl_seconds=60, persist_path=str(persist))
        assert mgr.active_count == 0

    def test_load_corrupt_json_is_safe(self, tmp_path) -> None:
        persist = tmp_path / "sessions.json"
        persist.write_text("{ not json")
        mgr = SessionManager(persist_path=str(persist))
        assert mgr.active_count == 0  # Skipped corrupt file gracefully.

    def test_save_is_atomic(self, tmp_path) -> None:
        persist = tmp_path / "sessions.json"
        mgr = SessionManager(persist_path=str(persist))
        mgr.get_or_create("a")
        mgr._save_to_disk()
        # The .tmp file should not remain on disk.
        assert not (tmp_path / "sessions.json.tmp").exists()
        assert persist.exists()
