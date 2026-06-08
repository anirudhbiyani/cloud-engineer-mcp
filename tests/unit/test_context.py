"""Tests for the ContextExtractor."""

from __future__ import annotations

from cloud_engineer_mcp.selector.context import ContextExtractor


class TestContextExtractor:
    def setup_method(self) -> None:
        self.extractor = ContextExtractor(max_tokens=512)

    def test_user_message_only(self) -> None:
        query = self.extractor.extract_query(user_message="Deploy an S3 bucket")
        assert "Deploy an S3 bucket" in query

    def test_with_tool_history(self) -> None:
        query = self.extractor.extract_query(
            user_message="What next?",
            tool_call_history=["aws_s3__list_buckets", "aws_s3__create_bucket"],
        )
        assert "Related tools" in query
        assert "list buckets" in query.lower() or "aws" in query.lower()

    def test_with_conversation_history(self) -> None:
        history = [
            {"role": "user", "content": "I need to manage Azure storage"},
            {"role": "assistant", "content": "I can help you with Azure Blob Storage."},
        ]
        query = self.extractor.extract_query(
            user_message="List the accounts",
            conversation_history=history,
        )
        assert "List the accounts" in query
        assert "Azure Blob Storage" in query

    def test_empty_input(self) -> None:
        query = self.extractor.extract_query()
        assert query == ""

    def test_truncation(self) -> None:
        extractor = ContextExtractor(max_tokens=10)
        long_message = "x" * 10000
        query = extractor.extract_query(user_message=long_message)
        assert len(query) <= 40  # 10 tokens * 4 chars

    def test_tool_history_namespacing_removed(self) -> None:
        query = self.extractor.extract_query(
            tool_call_history=["aws_ccapi__create_resource"],
        )
        assert "aws ccapi" in query.lower() or "create resource" in query.lower()

    def test_structured_intent_added_and_repeated(self) -> None:
        query = self.extractor.extract_query(
            user_message="Help me",
            action="create",
            resource_type="s3 bucket",
        )
        # action+resource appear twice (up-weighted) plus the user message once.
        assert query.count("create s3 bucket") == 2
        assert "Help me" in query

    def test_structured_intent_only_action(self) -> None:
        query = self.extractor.extract_query(
            user_message="anything",
            action="describe",
        )
        # No resource_type, so only action is in the intent string.
        assert query.startswith("describe describe")

    def test_combined_all_sources(self) -> None:
        query = self.extractor.extract_query(
            user_message="Deploy infrastructure",
            conversation_history=[
                {"role": "assistant", "content": "Using CDK for deployment"},
            ],
            tool_call_history=["aws_cdk__cdk_synth"],
        )
        assert "Deploy infrastructure" in query
        assert "cdk" in query.lower()
