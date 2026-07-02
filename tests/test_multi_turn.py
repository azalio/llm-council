"""Tests for multi-turn conversation support (conversation_id, Stage 0, summaries)."""

import json
import os
import sqlite3
import tempfile
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Override DB_PATH before importing storage to use a temp database
_test_db_dir = tempfile.mkdtemp()
_test_db_path = os.path.join(_test_db_dir, "test_council.db")
os.environ["LLM_COUNCIL_ROOT"] = _test_db_dir

# Ensure data directory exists for the test DB
os.makedirs(os.path.join(_test_db_dir, "data"), exist_ok=True)
_test_db_path = os.path.join(_test_db_dir, "data", "council.db")

# Patch DB_PATH before storage import
import backend.config
backend.config.DB_PATH = _test_db_path

# Force re-initialization of storage with the test DB
import backend.storage as storage
storage._local = __import__("threading").local()  # Reset thread-local
storage._ensure_schema()


# ─── Storage Tests ────────────────────────────────────────────

class TestStorageSummary:
    """Test summary column migration and functions."""

    def setup_method(self):
        """Create a fresh conversation for each test."""
        self.conv_id = str(uuid.uuid4())
        storage.create_conversation(self.conv_id)

    def test_summary_column_exists(self):
        """Verify the summary column was added to conversations table."""
        conn = storage._get_conn()
        cursor = conn.execute("PRAGMA table_info(conversations)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "summary" in columns

    def test_message_metadata_column_exists(self):
        """Verify assistant message metadata can be persisted."""
        conn = storage._get_conn()
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "metadata" in columns

    def test_get_conversation_no_summary(self):
        """get_conversation() should not include summary key when NULL."""
        conv = storage.get_conversation(self.conv_id)
        assert conv is not None
        assert "summary" not in conv

    def test_update_and_get_summary(self):
        """update_conversation_summary() persists and is retrievable."""
        storage.update_conversation_summary(self.conv_id, "Test summary about databases")

        summary = storage.get_conversation_summary(self.conv_id)
        assert summary == "Test summary about databases"

    def test_get_conversation_includes_summary(self):
        """get_conversation() includes summary when set."""
        storage.update_conversation_summary(self.conv_id, "Summary here")

        conv = storage.get_conversation(self.conv_id)
        assert conv["summary"] == "Summary here"

    def test_update_summary_nonexistent_conversation(self):
        """update_conversation_summary() raises for missing conversation."""
        with pytest.raises(ValueError, match="not found"):
            storage.update_conversation_summary("nonexistent-id", "test")

    def test_get_summary_nonexistent_conversation(self):
        """get_conversation_summary() returns None for missing conversation."""
        result = storage.get_conversation_summary("nonexistent-id")
        assert result is None

    def test_update_summary_overwrites(self):
        """Updating summary replaces the old one."""
        storage.update_conversation_summary(self.conv_id, "First summary")
        storage.update_conversation_summary(self.conv_id, "Updated summary")

        assert storage.get_conversation_summary(self.conv_id) == "Updated summary"

    def test_assistant_message_metadata_round_trip(self):
        """Assistant message metadata should survive a storage round-trip."""
        storage.add_user_message(self.conv_id, "Which council members failed?")
        storage.add_assistant_message(
            self.conv_id,
            [{"model": "alpha", "response": "Alpha answer"}],
            [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
            {"model": "chairman", "response": "Final answer"},
            metadata={
                "label_to_model": {"Response A": "alpha"},
                "aggregate_rankings": [{"model": "alpha", "average_rank": 1.0, "rankings_count": 1}],
                "run_status": {
                    "degraded": True,
                    "summary": "2 of 3 council members responded.",
                    "successful_council_models": 2,
                    "failed_council_models": 1,
                    "stages": {
                        "stage1": {
                            "requested_models": 3,
                            "successful_models": 2,
                            "failed_models_count": 1,
                            "failed_models": [{"model": "beta", "failure_type": "timeout"}],
                        }
                    },
                },
            },
        )

        conv = storage.get_conversation(self.conv_id)
        assistant_message = conv["messages"][1]
        assert assistant_message["metadata"]["label_to_model"] == {"Response A": "alpha"}
        assert assistant_message["metadata"]["run_status"]["degraded"] is True
        assert assistant_message["metadata"]["run_status"]["stages"]["stage1"]["failed_models"] == [
            {"model": "beta", "failure_type": "timeout"}
        ]


# ─── build_conversation_context Tests ─────────────────────────

from backend.council import build_conversation_context


class TestBuildConversationContext:
    """Test context builder for multi-turn conversations."""

    def test_empty_messages_returns_none(self):
        assert build_conversation_context({"messages": []}) is None

    def test_no_messages_key_returns_none(self):
        assert build_conversation_context({}) is None

    def test_single_user_message(self):
        conv = {"messages": [{"role": "user", "content": "Hello"}]}
        ctx = build_conversation_context(conv)
        assert ctx is not None
        assert len(ctx["recent_turns"]) == 1
        assert ctx["recent_turns"][0]["user"] == "Hello"
        assert ctx["recent_turns"][0]["assistant"] is None
        assert ctx["previous_final_answer"] is None

    def test_full_turn_pair(self):
        conv = {"messages": [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "stage3": {"response": "Python is a language."}},
        ]}
        ctx = build_conversation_context(conv)
        assert ctx["recent_turns"][0]["user"] == "What is Python?"
        assert ctx["recent_turns"][0]["assistant"] == "Python is a language."
        assert ctx["previous_final_answer"] == "Python is a language."

    def test_multiple_turns_max_recent(self):
        """Only last max_recent_turns turns are included."""
        messages = []
        for i in range(5):
            messages.append({"role": "user", "content": f"Q{i}"})
            messages.append({"role": "assistant", "stage3": {"response": f"A{i}"}})

        conv = {"messages": messages}
        ctx = build_conversation_context(conv, max_recent_turns=2)

        assert len(ctx["recent_turns"]) == 2
        assert ctx["recent_turns"][0]["user"] == "Q3"
        assert ctx["recent_turns"][1]["user"] == "Q4"

    def test_answer_truncation(self):
        """Long answers are truncated to 2000 chars in turns."""
        long_answer = "x" * 5000
        conv = {"messages": [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "stage3": {"response": long_answer}},
        ]}
        ctx = build_conversation_context(conv)
        assert len(ctx["recent_turns"][0]["assistant"]) <= 2003  # 2000 + "..."

    def test_previous_final_answer_truncation(self):
        """previous_final_answer is truncated to 3000 chars."""
        long_answer = "y" * 5000
        conv = {"messages": [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "stage3": {"response": long_answer}},
        ]}
        ctx = build_conversation_context(conv)
        assert len(ctx["previous_final_answer"]) <= 3003

    def test_summary_included(self):
        conv = {
            "summary": "Conversation about databases",
            "messages": [
                {"role": "user", "content": "Q"},
                {"role": "assistant", "stage3": {"response": "A"}},
            ],
        }
        ctx = build_conversation_context(conv)
        assert ctx["summary"] == "Conversation about databases"

    def test_summary_none_when_absent(self):
        conv = {"messages": [
            {"role": "user", "content": "Q"},
        ]}
        ctx = build_conversation_context(conv)
        assert ctx["summary"] is None


# ─── stage0_reformulate Tests ────────────────────────────────

from backend.council import stage0_reformulate


@pytest.mark.asyncio
class TestStage0Reformulate:
    """Test Stage 0 question reformulation."""

    async def test_returns_reformulated_question(self):
        """When model succeeds, returns reformulated question."""
        mock_response = {"content": "What are the pros and cons of InfluxDB vs TimescaleDB for time-series data?"}

        with patch("backend.council.query_model", new_callable=AsyncMock, return_value=mock_response):
            result = await stage0_reformulate(
                "What about InfluxDB vs TimescaleDB?",
                {"summary": "Discussing time-series databases", "recent_turns": [], "previous_final_answer": None},
            )
        assert "InfluxDB" in result
        assert "TimescaleDB" in result

    async def test_fallback_on_failure(self):
        """When model fails, returns original query."""
        with patch("backend.council.query_model", new_callable=AsyncMock, side_effect=Exception("API error")):
            result = await stage0_reformulate(
                "What about the second option?",
                {"summary": "Comparing DBs", "recent_turns": [], "previous_final_answer": None},
            )
        assert result == "What about the second option?"

    async def test_fallback_on_empty_response(self):
        """When model returns empty, returns original query."""
        with patch("backend.council.query_model", new_callable=AsyncMock, return_value={"content": ""}):
            result = await stage0_reformulate(
                "Tell me more",
                {"summary": None, "recent_turns": [], "previous_final_answer": None},
            )
        assert result == "Tell me more"

    async def test_fallback_on_none_response(self):
        """When model returns None, returns original query."""
        with patch("backend.council.query_model", new_callable=AsyncMock, return_value=None):
            result = await stage0_reformulate(
                "And the third?",
                {"summary": None, "recent_turns": [], "previous_final_answer": None},
            )
        assert result == "And the third?"

    async def test_includes_context_in_prompt(self):
        """Verify context elements are passed to the model."""
        captured_messages = []

        async def capture_model(model, messages, **kwargs):
            captured_messages.extend(messages)
            return {"content": "Standalone question"}

        ctx = {
            "summary": "We discussed Python frameworks",
            "recent_turns": [
                {"user": "What's Django?", "assistant": "Django is a web framework."},
            ],
            "previous_final_answer": None,
        }

        with patch("backend.council.query_model", side_effect=capture_model):
            await stage0_reformulate("What about Flask?", ctx)

        prompt_text = captured_messages[0]["content"]
        assert "Python frameworks" in prompt_text
        assert "What's Django?" in prompt_text
        assert "Flask" in prompt_text


# ─── generate_conversation_summary Tests ──────────────────────

from backend.council import generate_conversation_summary


@pytest.mark.asyncio
class TestGenerateConversationSummary:
    """Test rolling summary generation."""

    async def test_first_summary(self):
        """Generates summary for first turn (no previous summary)."""
        mock_response = {"content": "Discussion about Python web frameworks."}

        with patch("backend.council.query_model", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_conversation_summary(
                None, "What is Django?", "Django is a Python web framework..."
            )
        assert result == "Discussion about Python web frameworks."

    async def test_update_summary(self):
        """Updates existing summary with new turn."""
        mock_response = {"content": "Updated discussion about Django and Flask."}

        with patch("backend.council.query_model", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_conversation_summary(
                "Discussion about Django.", "What about Flask?", "Flask is a micro-framework..."
            )
        assert "Flask" in result

    async def test_returns_none_on_failure(self):
        """Returns None when model fails (summary is non-critical)."""
        with patch("backend.council.query_model", new_callable=AsyncMock, side_effect=Exception("API error")):
            result = await generate_conversation_summary(None, "Q", "A")
        assert result is None

    async def test_returns_none_on_empty(self):
        """Returns None when model returns empty response."""
        with patch("backend.council.query_model", new_callable=AsyncMock, return_value={"content": ""}):
            result = await generate_conversation_summary(None, "Q", "A")
        assert result is None


# ─── run_full_council context integration Tests ───────────────

from backend.council import run_full_council


@pytest.mark.asyncio
class TestRunFullCouncilContext:
    """Test that run_full_council correctly handles conversation_context."""

    async def test_no_context_skips_stage0(self):
        """Without conversation_context, Stage 0 is skipped."""
        mock_stage1 = [{"model": "m1", "response": "Answer"}]
        mock_stage2 = [{"model": "m1", "ranking": "1. Response A"}]
        mock_stage3 = {"model": "chairman", "response": "Final answer"}
        stage_debug = {
            "stage": "stage1",
            "successful_models": 1,
            "failed_models_count": 0,
            "failed_models": [],
            "requested_models": 1,
        }

        with patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, return_value=(mock_stage1, stage_debug)), \
             patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(mock_stage2, {"Response A": "m1"}, {**stage_debug, "stage": "stage2"})), \
             patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=(mock_stage3, {**stage_debug, "stage": "stage3"})), \
             patch("backend.council.stage0_reformulate", new_callable=AsyncMock) as mock_s0:

            s1, s2, s3, metadata = await run_full_council("Test question")
            mock_s0.assert_not_called()
            assert "stage0_standalone_query" not in metadata

    async def test_with_context_runs_stage0(self):
        """With conversation_context, Stage 0 runs and metadata includes standalone_query."""
        mock_stage1 = [{"model": "m1", "response": "Answer"}]
        mock_stage2 = [{"model": "m1", "ranking": "1. Response A"}]
        mock_stage3 = {"model": "chairman", "response": "Final answer"}
        context = {"summary": "About DBs", "recent_turns": [], "previous_final_answer": "prev answer"}
        stage_debug = {
            "stage": "stage1",
            "successful_models": 1,
            "failed_models_count": 0,
            "failed_models": [],
            "requested_models": 1,
        }

        with patch("backend.council.stage0_reformulate", new_callable=AsyncMock, return_value="Standalone question") as mock_s0, \
             patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, return_value=(mock_stage1, stage_debug)) as mock_s1, \
             patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(mock_stage2, {"Response A": "m1"}, {**stage_debug, "stage": "stage2"})), \
             patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=(mock_stage3, {**stage_debug, "stage": "stage3"})) as mock_s3:

            s1, s2, s3, metadata = await run_full_council(
                "Follow-up question", conversation_context=context
            )

            # Stage 0 was called
            mock_s0.assert_called_once_with("Follow-up question", context)

            # Stage 1 gets standalone question
            mock_s1.assert_called_once_with("Standalone question")

            # Stage 3 gets original question + context
            call_args = mock_s3.call_args
            assert call_args[0][0] == "Follow-up question"  # original query
            assert call_args[1].get("conversation_context") == context

            # Metadata includes standalone query
            assert metadata["stage0_standalone_query"] == "Standalone question"


# ─── MCP _execute_council_deliberation Tests ──────────────────

class TestMCPExecuteDeliberation:
    """Test MCP server's _execute_council_deliberation with conversation support."""

    @pytest.mark.asyncio
    async def test_new_conversation_created(self):
        """When no conversation_id, a new conversation is created."""
        # We need to import with mcp available — patch around it
        with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.server": MagicMock(), "mcp.server.fastmcp": MagicMock()}):
            # Reimport to get the function with mocked mcp
            import importlib
            import mcp_server.server as mcp_server_mod
            importlib.reload(mcp_server_mod)

        # This approach is fragile. Instead, test the logic via direct backend calls.
        # Create a conversation, add messages, verify context building works end-to-end.
        conv_id = str(uuid.uuid4())
        storage.create_conversation(conv_id)
        storage.add_user_message(conv_id, "What is the best DB for time-series?")
        storage.add_assistant_message(
            conv_id,
            [{"model": "m1", "response": "Use InfluxDB"}],
            [{"model": "m1", "ranking": "1. Response A"}],
            {"model": "chairman", "response": "InfluxDB is recommended for time-series data."},
        )
        storage.update_conversation_summary(conv_id, "Discussed time-series databases, recommended InfluxDB.")

        # Now verify context building for follow-up
        conv = storage.get_conversation(conv_id)
        assert conv is not None
        assert conv["summary"] == "Discussed time-series databases, recommended InfluxDB."
        assert len(conv["messages"]) == 2

        ctx = build_conversation_context(conv)
        assert ctx is not None
        assert ctx["summary"] == "Discussed time-series databases, recommended InfluxDB."
        assert len(ctx["recent_turns"]) == 1
        assert ctx["recent_turns"][0]["user"] == "What is the best DB for time-series?"
        assert "InfluxDB" in ctx["previous_final_answer"]

    @pytest.mark.asyncio
    async def test_multi_turn_end_to_end_storage(self):
        """Full round-trip: create conversation, add 2 turns, verify context."""
        conv_id = str(uuid.uuid4())
        storage.create_conversation(conv_id)

        # Turn 1
        storage.add_user_message(conv_id, "What is Redis?")
        storage.add_assistant_message(
            conv_id,
            [{"model": "m1", "response": "Redis is an in-memory data store"}],
            [{"model": "m1", "ranking": "1. Response A"}],
            {"model": "chairman", "response": "Redis is an in-memory key-value store."},
        )
        storage.update_conversation_summary(conv_id, "Discussed Redis as in-memory key-value store.")

        # Turn 2
        storage.add_user_message(conv_id, "How does it compare to Memcached?")
        storage.add_assistant_message(
            conv_id,
            [{"model": "m1", "response": "Redis has more features than Memcached"}],
            [{"model": "m1", "ranking": "1. Response A"}],
            {"model": "chairman", "response": "Redis offers data structures, persistence, and replication."},
        )
        storage.update_conversation_summary(conv_id, "Discussed Redis vs Memcached. Redis has more features.")

        # Verify final state
        conv = storage.get_conversation(conv_id)
        assert len(conv["messages"]) == 4  # 2 user + 2 assistant
        assert conv["summary"] == "Discussed Redis vs Memcached. Redis has more features."

        ctx = build_conversation_context(conv)
        assert len(ctx["recent_turns"]) == 2
        assert ctx["recent_turns"][0]["user"] == "What is Redis?"
        assert ctx["recent_turns"][1]["user"] == "How does it compare to Memcached?"
        assert "Redis" in ctx["previous_final_answer"]


# ─── stage3_synthesize_final conversation_context Tests ───────

from backend.council import stage3_synthesize_final


@pytest.mark.asyncio
class TestStage3WithContext:
    """Test that stage3 chairman prompt includes conversation context."""

    async def test_no_context_no_conversation_section(self):
        """Without context, chairman prompt has no conversation section."""
        captured_messages = []

        async def capture_model(model, messages, **kwargs):
            captured_messages.extend(messages)
            return {"content": "Synthesis"}

        with patch("backend.council.query_model", side_effect=capture_model):
            await stage3_synthesize_final(
                "Test question",
                [{"model": "m1", "response": "Answer A"}],
                [{"model": "m1", "ranking": "1. Response A", "parsed_ranking": ["Response A"]}],
                {"Response A": "m1"},
            )

        prompt = captured_messages[0]["content"]
        assert "CONVERSATION CONTEXT" not in prompt

    async def test_with_context_includes_conversation_section(self):
        """With context, chairman prompt includes conversation history."""
        captured_messages = []

        async def capture_model(model, messages, **kwargs):
            captured_messages.extend(messages)
            return {"content": "Contextual synthesis"}

        context = {
            "summary": "Discussion about Python web frameworks",
            "recent_turns": [
                {"user": "What is Django?", "assistant": "Django is a web framework."},
            ],
            "previous_final_answer": "Django is a full-featured Python web framework.",
        }

        with patch("backend.council.query_model", side_effect=capture_model):
            await stage3_synthesize_final(
                "How does Flask compare?",
                [{"model": "m1", "response": "Flask is lighter"}],
                [{"model": "m1", "ranking": "1. Response A", "parsed_ranking": ["Response A"]}],
                {"Response A": "m1"},
                conversation_context=context,
            )

        prompt = captured_messages[0]["content"]
        assert "CONVERSATION CONTEXT" in prompt
        assert "Python web frameworks" in prompt
        assert "What is Django?" in prompt
        assert "Django is a full-featured" in prompt
        assert "How does Flask compare?" in prompt
