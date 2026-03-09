from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.thinking_tools import (
    ThinkingEngine,
    ThoughtData,
)


@pytest.fixture()
def thinking_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_config(thinking_dir: Path) -> Iterator[None]:
    with (
        patch("cocoindex_code.thinking_tools.config") as mock_config,
        patch("cocoindex_code.thinking_tools._engine", None),
    ):
        mock_config.index_dir = thinking_dir
        yield


def _make_thought(
    thought: str = "t",
    thought_number: int = 1,
    total_thoughts: int = 3,
    next_thought_needed: bool = True,
    **kwargs,
) -> ThoughtData:
    return ThoughtData(
        thought=thought,
        thought_number=thought_number,
        total_thoughts=total_thoughts,
        next_thought_needed=next_thought_needed,
        **kwargs,
    )


class TestThinkingEngine:
    def test_init_creates_engine(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        assert engine._sessions == {}

    def test_load_empty_memory(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        assert engine._learnings == []
        assert engine._strategy_scores == {}

    def test_process_basic_thought(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        data = _make_thought(thought="first", thought_number=1, total_thoughts=3)
        result = engine.process_thought("s1", data)
        assert result.success
        assert result.session_id == "s1"
        assert result.thought_number == 1
        assert result.total_thoughts == 3
        assert result.thought_history_length == 1

    def test_process_multiple_thoughts(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        for i in range(1, 4):
            result = engine.process_thought("s1", _make_thought(thought_number=i))
            assert result.thought_history_length == i

    def test_auto_adjust_total_thoughts(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_thought("s1", _make_thought(thought_number=5, total_thoughts=3))
        assert result.total_thoughts == 5

    def test_branching(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_thought("s1", _make_thought())
        engine.process_thought(
            "s1", _make_thought(thought_number=2, branch_id="b1", branch_from_thought=1)
        )
        result = engine.process_thought(
            "s1", _make_thought(thought_number=3, branch_id="b2", branch_from_thought=1)
        )
        assert "b1" in result.branches
        assert "b2" in result.branches

    def test_multiple_thoughts_same_branch(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_thought(
            "s1", _make_thought(thought_number=1, branch_id="b1", branch_from_thought=1)
        )
        result = engine.process_thought(
            "s1", _make_thought(thought_number=2, branch_id="b1", branch_from_thought=1)
        )
        assert len(result.branches) == 1


class TestExtendedThinking:
    def test_basic_extended(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_extended_thought("s1", _make_thought(), depth_level="deep")
        assert result.depth_level == "deep"

    def test_checkpoint_at_interval(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_extended_thought(
            "s1",
            _make_thought(thought_number=5, total_thoughts=10),
            checkpoint_interval=5,
        )
        assert result.checkpoint_summary != ""

    def test_no_checkpoint_between_intervals(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_extended_thought(
            "s1",
            _make_thought(thought_number=3, total_thoughts=10),
            checkpoint_interval=5,
        )
        assert result.checkpoint_summary == ""

    def test_exhaustive_mode(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_extended_thought("s1", _make_thought(), depth_level="exhaustive")
        assert result.depth_level == "exhaustive"

    def test_steps_since_checkpoint(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_extended_thought(
            "s1",
            _make_thought(thought_number=7, total_thoughts=10),
            checkpoint_interval=5,
        )
        assert result.steps_since_checkpoint == 2


class TestUltraThinking:
    def test_explore_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_ultra_thought("s1", _make_thought(), phase="explore")
        assert result.phase == "explore"

    def test_hypothesize_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_ultra_thought(
            "s1", _make_thought(), phase="hypothesize", hypothesis="H1"
        )
        assert "H1" in result.hypotheses

    def test_verify_high_confidence(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_ultra_thought("s1", _make_thought(), phase="verify", confidence=0.9)
        assert result.verification_status == "supported"

    def test_verify_medium_confidence(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_ultra_thought("s1", _make_thought(), phase="verify", confidence=0.5)
        assert result.verification_status == "partially_supported"

    def test_verify_low_confidence(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_ultra_thought("s1", _make_thought(), phase="verify", confidence=0.2)
        assert result.verification_status == "unsupported"

    def test_synthesize_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_ultra_thought(
            "s1", _make_thought(thought_number=1), phase="hypothesize", hypothesis="H1"
        )
        engine.process_ultra_thought(
            "s1", _make_thought(thought_number=2), phase="hypothesize", hypothesis="H2"
        )
        result = engine.process_ultra_thought(
            "s1", _make_thought(thought_number=3), phase="synthesize"
        )
        assert "Synthesis" in result.synthesis

    def test_multiple_hypotheses(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        for i, h in enumerate(["H1", "H2", "H3"], start=1):
            engine.process_ultra_thought(
                "s1", _make_thought(thought_number=i), phase="hypothesize", hypothesis=h
            )
        result = engine.process_ultra_thought(
            "s1", _make_thought(thought_number=4), phase="explore"
        )
        assert "H1" in result.hypotheses
        assert "H2" in result.hypotheses
        assert "H3" in result.hypotheses


class TestLearningLoop:
    def test_record_learning(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.record_learning("s1", "divide_conquer", ["success"], 0.8, ["insight1"])
        assert result.success
        assert result.learnings_extracted == 1

    def test_learning_persisted(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "divide_conquer", ["success"], 0.8, ["insight1"])
        engine2 = ThinkingEngine(thinking_dir)
        assert len(engine2._learnings) >= 1

    def test_strategy_score_updated(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "divide_conquer", ["success"], 0.8, ["insight1"])
        score = engine._strategy_scores["divide_conquer"]
        assert score.usage_count == 1
        assert score.avg_reward == pytest.approx(0.8)

    def test_multiple_learnings_same_strategy(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "divide_conquer", ["success"], 0.8, ["i1"])
        engine.record_learning("s2", "divide_conquer", ["partial"], 0.4, ["i2"])
        score = engine._strategy_scores["divide_conquer"]
        assert score.avg_reward == pytest.approx(0.6)


class TestSelfImprove:
    def test_no_learnings(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        recs = engine.get_strategy_recommendations()
        assert recs == []

    def test_recommendations_sorted(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "low", [], 0.2, [])
        engine.record_learning("s2", "mid", [], 0.5, [])
        engine.record_learning("s3", "high", [], 0.9, [])
        recs = engine.get_strategy_recommendations()
        assert recs[0].strategy == "high"
        assert recs[1].strategy == "mid"
        assert recs[2].strategy == "low"

    def test_top_k_limit(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        for i in range(5):
            engine.record_learning(f"s{i}", f"strat{i}", [], float(i) / 10, [])
        recs = engine.get_strategy_recommendations(top_k=2)
        assert len(recs) == 2


class TestRewardThinking:
    def test_apply_reward(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "strat", [], 0.3, [])
        result = engine.apply_reward("s1", 0.5)
        assert result.success
        assert result.new_reward == pytest.approx(0.5)

    def test_apply_reward_no_session(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.apply_reward("nonexistent", 0.5)
        assert result.success is False

    def test_cumulative_reward(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "strat", [], 0.3, [])
        result = engine.apply_reward("s1", 0.2)
        assert result.cumulative_reward == pytest.approx(0.5)


class TestPersistence:
    def test_strategy_persisted(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "persist_strat", [], 0.7, [])
        engine2 = ThinkingEngine(thinking_dir)
        assert "persist_strat" in engine2._strategy_scores

    def test_memory_file_created(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.record_learning("s1", "strat", [], 0.5, [])
        assert (thinking_dir / "thinking_memory.jsonl").exists()
