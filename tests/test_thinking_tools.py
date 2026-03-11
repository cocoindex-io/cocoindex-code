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


# --- Helper to set up hypotheses for evidence tests ---


def _setup_hypotheses(engine: ThinkingEngine, session_id: str, hypotheses: list[str]) -> None:
    """Add hypotheses to a session via ultra_thinking."""
    for i, h in enumerate(hypotheses, start=1):
        engine.process_ultra_thought(
            session_id,
            _make_thought(thought_number=i, total_thoughts=len(hypotheses)),
            phase="hypothesize",
            hypothesis=h,
        )


class TestEvidenceTracker:
    def test_add_evidence_to_hypothesis(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1", "H2"])
        result = engine.add_evidence("s1", 0, "Found in auth.py", "code_ref", 0.8)
        assert result.success
        assert result.hypothesis_index == 0
        assert result.hypothesis_text == "H1"
        assert result.total_evidence_count == 1
        assert result.cumulative_strength == pytest.approx(0.8)
        assert result.effort_mode == "medium"

    def test_add_evidence_no_session(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.add_evidence("nonexistent", 0, "text", "data_point", 0.5)
        assert result.success is False
        assert "No hypotheses" in (result.message or "")

    def test_add_evidence_invalid_index(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.add_evidence("s1", 5, "text", "data_point", 0.5)
        assert result.success is False
        assert "out of range" in (result.message or "")

    def test_add_evidence_no_hypotheses(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_thought("s1", _make_thought())
        result = engine.add_evidence("s1", 0, "text", "data_point", 0.5)
        assert result.success is False

    def test_list_evidence(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        engine.add_evidence("s1", 0, "ev1", "code_ref", 0.7)
        engine.add_evidence("s1", 0, "ev2", "data_point", 0.9)
        result = engine.get_evidence("s1", 0)
        assert result.success
        assert result.total_evidence_count == 2
        assert result.evidence[0].text == "ev1"
        assert result.evidence[1].text == "ev2"

    def test_list_evidence_empty(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.get_evidence("s1", 0)
        assert result.success
        assert result.total_evidence_count == 0
        assert result.cumulative_strength == pytest.approx(0.0)

    def test_cumulative_strength(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        engine.add_evidence("s1", 0, "ev1", "code_ref", 0.6)
        engine.add_evidence("s1", 0, "ev2", "data_point", 0.8)
        result = engine.add_evidence("s1", 0, "ev3", "external", 1.0)
        assert result.cumulative_strength == pytest.approx((0.6 + 0.8 + 1.0) / 3)

    def test_multiple_hypotheses_evidence(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1", "H2"])
        engine.add_evidence("s1", 0, "ev-a", "code_ref", 0.5)
        engine.add_evidence("s1", 1, "ev-b", "assumption", 0.3)
        result_0 = engine.get_evidence("s1", 0)
        result_1 = engine.get_evidence("s1", 1)
        assert result_0.total_evidence_count == 1
        assert result_1.total_evidence_count == 1

    def test_all_evidence_types(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        for etype in ["code_ref", "data_point", "external", "assumption", "test_result"]:
            result = engine.add_evidence("s1", 0, f"ev-{etype}", etype, 0.5)
            assert result.success, f"Failed for type {etype}"

    def test_invalid_evidence_type(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.add_evidence("s1", 0, "text", "invalid_type", 0.5)
        assert result.success is False
        assert "Invalid evidence_type" in (result.message or "")

    def test_strength_clamped(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.add_evidence("s1", 0, "strong", "data_point", 1.5)
        assert result.success
        assert result.evidence[0].strength == pytest.approx(1.0)
        result2 = engine.add_evidence("s1", 0, "weak", "data_point", -0.5)
        assert result2.evidence[1].strength == pytest.approx(0.0)

    def test_low_effort_skips_type_validation(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.add_evidence(
            "s1", 0, "text", "bogus_type", 0.5, effort_mode="low"
        )
        assert result.success
        assert result.evidence[0].evidence_type == "data_point"
        assert result.effort_mode == "low"

    def test_high_effort_validates_type(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        _setup_hypotheses(engine, "s1", ["H1"])
        result = engine.add_evidence(
            "s1", 0, "text", "bad", 0.5, effort_mode="high"
        )
        assert result.success is False


class TestPremortem:
    def test_describe_plan_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem(
            "s1", _make_thought(), phase="describe_plan", plan="Migrate DB"
        )
        assert result.success
        assert result.phase == "describe_plan"
        assert result.plan_description == "Migrate DB"
        assert result.effort_mode == "medium"

    def test_imagine_failure_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_premortem("s1", _make_thought(), phase="describe_plan", plan="My plan")
        result = engine.process_premortem(
            "s1", _make_thought(thought_number=2),
            phase="imagine_failure", failure_scenario="Data loss",
        )
        assert result.success
        assert result.failure_scenario == "Data loss"
        assert result.plan_description == "My plan"

    def test_identify_causes_adds_risk(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem(
            "s1", _make_thought(), phase="identify_causes",
            risk_description="No backup", likelihood=0.7, impact=0.9,
        )
        assert result.success
        assert len(result.risks) == 1
        assert result.risks[0].risk_score == pytest.approx(0.7 * 0.9)

    def test_identify_causes_requires_description(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem("s1", _make_thought(), phase="identify_causes")
        assert result.success is False
        assert "risk_description is required" in (result.message or "")

    def test_rank_risks_by_score(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_premortem(
            "s1", _make_thought(thought_number=1),
            phase="identify_causes", risk_description="Low", likelihood=0.2, impact=0.3,
        )
        engine.process_premortem(
            "s1", _make_thought(thought_number=2),
            phase="identify_causes", risk_description="High", likelihood=0.9, impact=0.9,
        )
        result = engine.process_premortem(
            "s1", _make_thought(thought_number=3), phase="rank_risks",
        )
        assert result.ranked_risks[0].description == "High"
        assert result.ranked_risks[1].description == "Low"

    def test_mitigate_risk(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_premortem(
            "s1", _make_thought(thought_number=1),
            phase="identify_causes", risk_description="Risk A", likelihood=0.5, impact=0.5,
        )
        result = engine.process_premortem(
            "s1", _make_thought(thought_number=2),
            phase="mitigate", risk_index=0, mitigation="Add backups",
        )
        assert result.success
        assert result.risks[0].mitigation == "Add backups"
        assert result.mitigations_count == 1

    def test_mitigate_invalid_index(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_premortem(
            "s1", _make_thought(), phase="identify_causes",
            risk_description="R", likelihood=0.5, impact=0.5,
        )
        result = engine.process_premortem(
            "s1", _make_thought(thought_number=2),
            phase="mitigate", risk_index=5, mitigation="nope",
        )
        assert result.success is False
        assert "out of range" in (result.message or "")

    def test_mitigate_requires_risk_index(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_premortem(
            "s1", _make_thought(), phase="identify_causes",
            risk_description="R", likelihood=0.5, impact=0.5,
        )
        result = engine.process_premortem(
            "s1", _make_thought(thought_number=2), phase="mitigate", mitigation="fix",
        )
        assert result.success is False
        assert "risk_index is required" in (result.message or "")

    def test_invalid_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem("s1", _make_thought(), phase="bad_phase")
        assert result.success is False
        assert "Invalid phase" in (result.message or "")

    def test_likelihood_impact_clamped(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem(
            "s1", _make_thought(), phase="identify_causes",
            risk_description="R", likelihood=1.5, impact=-0.3,
        )
        assert result.risks[0].likelihood == pytest.approx(1.0)
        assert result.risks[0].impact == pytest.approx(0.0)
        assert result.risks[0].risk_score == pytest.approx(0.0)

    def test_effort_mode_passed_through(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_premortem(
            "s1", _make_thought(), phase="describe_plan",
            plan="p", effort_mode="high",
        )
        assert result.effort_mode == "high"

    def test_full_flow(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        r1 = engine.process_premortem(
            "s1", _make_thought(thought_number=1, total_thoughts=5),
            phase="describe_plan", plan="Deploy auth",
        )
        assert r1.success
        r2 = engine.process_premortem(
            "s1", _make_thought(thought_number=2, total_thoughts=5),
            phase="imagine_failure", failure_scenario="Tokens rejected",
        )
        assert r2.success
        r3 = engine.process_premortem(
            "s1", _make_thought(thought_number=3, total_thoughts=5),
            phase="identify_causes", risk_description="Format mismatch",
            likelihood=0.6, impact=0.9,
        )
        assert r3.success
        r4 = engine.process_premortem(
            "s1", _make_thought(thought_number=4, total_thoughts=5), phase="rank_risks",
        )
        assert len(r4.ranked_risks) == 1
        r5 = engine.process_premortem(
            "s1", _make_thought(thought_number=5, total_thoughts=5, next_thought_needed=False),
            phase="mitigate", risk_index=0, mitigation="Backward-compat parsing",
        )
        assert r5.mitigations_count == 1


class TestInversionThinking:
    def test_define_goal(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion(
            "s1", _make_thought(), phase="define_goal", goal="Ship on time",
        )
        assert result.success
        assert result.goal == "Ship on time"
        assert result.effort_mode == "medium"

    def test_invert_auto_generates(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="define_goal", goal="Ship on time",
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="invert",
        )
        assert result.success
        assert "guarantee failure" in result.inverted_goal

    def test_invert_custom(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="define_goal", goal="Ship on time",
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="invert",
            inverted_goal="How to guarantee we miss the deadline",
        )
        assert result.inverted_goal == "How to guarantee we miss the deadline"

    def test_list_failure_causes(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="No testing", severity=0.8,
        )
        assert result.success
        assert len(result.failure_causes) == 1
        assert result.failure_causes[0].description == "No testing"
        assert result.failure_causes[0].severity == pytest.approx(0.8)

    def test_list_failure_causes_requires_cause(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
        )
        assert result.success is False
        assert "failure_cause is required" in (result.message or "")

    def test_rank_causes(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(thought_number=1), phase="list_failure_causes",
            failure_cause="Low sev", severity=0.2,
        )
        engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="list_failure_causes",
            failure_cause="High sev", severity=0.9,
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=3), phase="rank_causes",
        )
        assert result.success
        assert result.ranked_causes[0].description == "High sev"
        assert result.ranked_causes[1].description == "Low sev"

    def test_rank_causes_blocked_in_low_effort(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="C1", severity=0.5, effort_mode="low",
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="rank_causes",
            effort_mode="low",
        )
        assert result.success is False
        assert "not available in low effort" in (result.message or "")

    def test_reinvert(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="No testing", severity=0.8,
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="reinvert",
            cause_index=0, inverted_action="Add comprehensive test suite",
        )
        assert result.success
        assert result.failure_causes[0].inverted_action == "Add comprehensive test suite"

    def test_reinvert_requires_cause_index(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="C1", severity=0.5,
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="reinvert",
        )
        assert result.success is False
        assert "cause_index is required" in (result.message or "")

    def test_reinvert_invalid_index(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="C1", severity=0.5,
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="reinvert",
            cause_index=99,
        )
        assert result.success is False
        assert "out of range" in (result.message or "")

    def test_action_plan(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion(
            "s1", _make_thought(), phase="action_plan",
            action_item="Write integration tests",
        )
        assert result.success
        assert "Write integration tests" in result.action_plan

    def test_action_plan_high_effort_auto_populate(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_inversion(
            "s1", _make_thought(thought_number=1), phase="list_failure_causes",
            failure_cause="No tests", severity=0.8, effort_mode="high",
        )
        engine.process_inversion(
            "s1", _make_thought(thought_number=2), phase="reinvert",
            cause_index=0, inverted_action="Add tests", effort_mode="high",
        )
        result = engine.process_inversion(
            "s1", _make_thought(thought_number=3), phase="action_plan",
            effort_mode="high",
        )
        assert result.success
        assert "Add tests" in result.action_plan

    def test_invalid_phase(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion("s1", _make_thought(), phase="bad")
        assert result.success is False
        assert "Invalid phase" in (result.message or "")

    def test_severity_clamped(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_inversion(
            "s1", _make_thought(), phase="list_failure_causes",
            failure_cause="C", severity=2.0,
        )
        assert result.failure_causes[0].severity == pytest.approx(1.0)

    def test_full_flow(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        r1 = engine.process_inversion(
            "s1", _make_thought(thought_number=1, total_thoughts=6),
            phase="define_goal", goal="Launch v2",
        )
        assert r1.success
        r2 = engine.process_inversion(
            "s1", _make_thought(thought_number=2, total_thoughts=6),
            phase="invert",
        )
        assert r2.success
        r3 = engine.process_inversion(
            "s1", _make_thought(thought_number=3, total_thoughts=6),
            phase="list_failure_causes", failure_cause="Skip QA", severity=0.9,
        )
        assert r3.success
        r4 = engine.process_inversion(
            "s1", _make_thought(thought_number=4, total_thoughts=6),
            phase="rank_causes",
        )
        assert len(r4.ranked_causes) == 1
        r5 = engine.process_inversion(
            "s1", _make_thought(thought_number=5, total_thoughts=6),
            phase="reinvert", cause_index=0, inverted_action="Mandatory QA gate",
        )
        assert r5.success
        r6 = engine.process_inversion(
            "s1", _make_thought(thought_number=6, total_thoughts=6, next_thought_needed=False),
            phase="action_plan", action_item="Enforce CI QA step",
        )
        assert "Enforce CI QA step" in r6.action_plan


class TestEffortEstimator:
    def test_add_estimate(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="Build API",
            optimistic=2.0, likely=4.0, pessimistic=8.0,
        )
        assert result.success
        assert len(result.estimates) == 1
        assert result.estimates[0].task == "Build API"
        assert result.effort_mode == "medium"

    def test_pert_calculation(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=3.0, pessimistic=5.0,
        )
        # PERT = (1 + 4*3 + 5) / 6 = 18/6 = 3.0
        assert result.estimates[0].pert_estimate == pytest.approx(3.0)
        # std_dev = (5 - 1) / 6 ≈ 0.667
        assert result.estimates[0].std_dev == pytest.approx(4.0 / 6.0)

    def test_confidence_intervals(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=3.0, pessimistic=5.0,
        )
        est = result.estimates[0]
        assert est.confidence_68_low == pytest.approx(est.pert_estimate - est.std_dev)
        assert est.confidence_68_high == pytest.approx(est.pert_estimate + est.std_dev)
        assert est.confidence_95_low == pytest.approx(est.pert_estimate - 2 * est.std_dev)
        assert est.confidence_95_high == pytest.approx(est.pert_estimate + 2 * est.std_dev)

    def test_add_requires_task(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", optimistic=1.0, likely=2.0, pessimistic=3.0,
        )
        assert result.success is False
        assert "task name is required" in (result.message or "")

    def test_pessimistic_must_be_gte_optimistic(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=5.0, likely=3.0, pessimistic=1.0,
        )
        assert result.success is False
        assert "pessimistic must be >= optimistic" in (result.message or "")

    def test_multiple_estimates_total(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=2.0, pessimistic=3.0,
        )
        result = engine.process_estimate(
            "s1", action="add", task="T2",
            optimistic=2.0, likely=4.0, pessimistic=6.0,
        )
        assert len(result.estimates) == 2
        assert result.total_pert == pytest.approx(
            result.estimates[0].pert_estimate + result.estimates[1].pert_estimate
        )

    def test_summary_action(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=2.0, pessimistic=3.0,
        )
        result = engine.process_estimate("s1", action="summary")
        assert result.success
        assert len(result.estimates) == 1

    def test_clear_action(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=2.0, pessimistic=3.0,
        )
        result = engine.process_estimate("s1", action="clear")
        assert result.success
        assert "cleared" in (result.message or "").lower()

    def test_invalid_action(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate("s1", action="bad")
        assert result.success is False
        assert "Invalid action" in (result.message or "")

    def test_low_effort_single_point(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=0.0, likely=5.0, pessimistic=0.0,
            effort_mode="low",
        )
        assert result.success
        est = result.estimates[0]
        assert est.pert_estimate == pytest.approx(5.0)
        assert est.optimistic == pytest.approx(5.0)
        assert est.pessimistic == pytest.approx(5.0)
        assert result.total_std_dev == pytest.approx(0.0)

    def test_medium_effort_has_68_ci(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=3.0, pessimistic=5.0,
            effort_mode="medium",
        )
        assert result.total_confidence_68_low != 0.0
        assert result.total_confidence_68_high != 0.0
        # Medium does not populate 95% CI
        assert result.total_confidence_95_low == pytest.approx(0.0)

    def test_high_effort_has_95_ci(self, thinking_dir: Path) -> None:
        engine = ThinkingEngine(thinking_dir)
        result = engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=3.0, pessimistic=5.0,
            effort_mode="high",
        )
        assert result.total_confidence_68_low != 0.0
        assert result.total_confidence_95_low != 0.0
        assert result.total_confidence_95_high != 0.0

    def test_total_std_dev_is_rss(self, thinking_dir: Path) -> None:
        """Total std_dev should be root-sum-square of individual std_devs."""
        engine = ThinkingEngine(thinking_dir)
        engine.process_estimate(
            "s1", action="add", task="T1",
            optimistic=1.0, likely=2.0, pessimistic=5.0,
        )
        result = engine.process_estimate(
            "s1", action="add", task="T2",
            optimistic=2.0, likely=4.0, pessimistic=8.0,
        )
        expected = (
            result.estimates[0].std_dev ** 2
            + result.estimates[1].std_dev ** 2
        ) ** 0.5
        assert result.total_std_dev == pytest.approx(expected)
