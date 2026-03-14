"""MCP tool registration for the thinking tools subsystem.

This module registers all thinking-related MCP tools (sequential_thinking,
extended_thinking, ultra_thinking, evidence_tracker, premortem,
inversion_thinking, effort_estimator, learning_loop, self_improve,
reward_thinking, plan_optimizer) on a FastMCP server instance.

Models are defined in thinking_models.py and the ThinkingEngine
lives in thinking_engine.py.
"""

from __future__ import annotations

import uuid

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import config
from .thinking_engine import ThinkingEngine

# Re-export all public symbols so existing imports like
#   from cocoindex_code.thinking_tools import ThinkingEngine, ThoughtData
# continue to work without changes.
from .thinking_models import (  # noqa: F401
    PLAN_DIMENSIONS,
    THINKING_MEMORY_FILE,
    VALID_EFFORT_MODES,
    VALID_EVIDENCE_TYPES,
    VALID_INVERSION_PHASES,
    VALID_PLAN_OPTIMIZER_PHASES,
    VALID_PREMORTEM_PHASES,
    EffortEstimatorResult,
    EstimateItem,
    EstimatorSession,
    EvidenceItem,
    EvidenceTrackerResult,
    ExtendedThinkingResult,
    InversionCause,
    InversionSession,
    InversionThinkingResult,
    LearningEntry,
    LearningLoopResult,
    PlanAntiPattern,
    PlanOptimizerResult,
    PlanOptimizerSession,
    PlanVariant,
    PremortemResult,
    PremortemRisk,
    PremortemSession,
    RewardResult,
    SelfImproveResult,
    StrategyScore,
    ThinkingResult,
    ThoughtData,
    UltraThinkingResult,
)

_engine: ThinkingEngine | None = None


def _get_engine() -> ThinkingEngine:
    global _engine
    if _engine is None:
        _engine = ThinkingEngine(config.index_dir)
    return _engine


def register_thinking_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        name="sequential_thinking",
        description=(
            "Step-by-step problem solving with branching and revision support."
            " Each thought builds on previous ones, with ability to revise earlier"
            " thoughts, branch into alternative reasoning paths, and dynamically"
            " adjust the total number of thoughts as understanding deepens."
        ),
    )
    async def sequential_thinking(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed (can be adjusted).",
        ),
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        is_revision: bool = Field(
            default=False,
            description="Whether this thought revises a previous one.",
        ),
        revises_thought: int | None = Field(
            default=None,
            description="Which thought number is being revised.",
        ),
        branch_from_thought: int | None = Field(
            default=None,
            description="Thought number to branch from.",
        ),
        branch_id: str | None = Field(
            default=None,
            description="Identifier for the current branch.",
        ),
        needs_more_thoughts: bool = Field(
            default=False,
            description="Signal that more thoughts are needed beyond the current total.",
        ),
    ) -> ThinkingResult:
        try:
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
                is_revision=is_revision,
                revises_thought=revises_thought,
                branch_from_thought=branch_from_thought,
                branch_id=branch_id,
                needs_more_thoughts=needs_more_thoughts,
            )
            return engine.process_thought(sid, data)
        except Exception as e:
            return ThinkingResult(success=False, message=f"Thinking failed: {e!s}")

    @mcp.tool(
        name="extended_thinking",
        description=(
            "Deeper analysis with automatic checkpoints."
            " Extends sequential thinking with configurable depth levels"
            " (standard, deep, exhaustive) and periodic checkpoint summaries"
            " to maintain coherence over long reasoning chains."
        ),
    )
    async def extended_thinking(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed (can be adjusted).",
        ),
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        is_revision: bool = Field(
            default=False,
            description="Whether this thought revises a previous one.",
        ),
        revises_thought: int | None = Field(
            default=None,
            description="Which thought number is being revised.",
        ),
        branch_from_thought: int | None = Field(
            default=None,
            description="Thought number to branch from.",
        ),
        branch_id: str | None = Field(
            default=None,
            description="Identifier for the current branch.",
        ),
        needs_more_thoughts: bool = Field(
            default=False,
            description="Signal that more thoughts are needed beyond the current total.",
        ),
        depth_level: str = Field(
            default="deep",
            description="Depth of analysis: 'standard', 'deep', or 'exhaustive'.",
        ),
        checkpoint_interval: int = Field(
            default=5,
            ge=1,
            le=50,
            description="Number of steps between automatic checkpoints.",
        ),
    ) -> ExtendedThinkingResult:
        try:
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
                is_revision=is_revision,
                revises_thought=revises_thought,
                branch_from_thought=branch_from_thought,
                branch_id=branch_id,
                needs_more_thoughts=needs_more_thoughts,
            )
            return engine.process_extended_thought(sid, data, depth_level, checkpoint_interval)
        except Exception as e:
            return ExtendedThinkingResult(success=False, message=f"Extended thinking failed: {e!s}")

    @mcp.tool(
        name="ultra_thinking",
        description=(
            "Maximum-depth reasoning with hypothesis generation, verification,"
            " and synthesis. Supports phased thinking through explore, hypothesize,"
            " verify, synthesize, and refine stages for complex problem solving."
        ),
    )
    async def ultra_thinking(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed (can be adjusted).",
        ),
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        is_revision: bool = Field(
            default=False,
            description="Whether this thought revises a previous one.",
        ),
        revises_thought: int | None = Field(
            default=None,
            description="Which thought number is being revised.",
        ),
        branch_from_thought: int | None = Field(
            default=None,
            description="Thought number to branch from.",
        ),
        branch_id: str | None = Field(
            default=None,
            description="Identifier for the current branch.",
        ),
        needs_more_thoughts: bool = Field(
            default=False,
            description="Signal that more thoughts are needed beyond the current total.",
        ),
        phase: str = Field(
            default="explore",
            description=(
                "Thinking phase: 'explore', 'hypothesize', 'verify', 'synthesize', or 'refine'."
            ),
        ),
        hypothesis: str | None = Field(
            default=None,
            description="A hypothesis to register during the 'hypothesize' phase.",
        ),
        confidence: float = Field(
            default=0.0,
            ge=0,
            le=1,
            description="Confidence level for verification (0.0 to 1.0).",
        ),
    ) -> UltraThinkingResult:
        try:
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
                is_revision=is_revision,
                revises_thought=revises_thought,
                branch_from_thought=branch_from_thought,
                branch_id=branch_id,
                needs_more_thoughts=needs_more_thoughts,
            )
            return engine.process_ultra_thought(sid, data, phase, hypothesis, confidence)
        except Exception as e:
            return UltraThinkingResult(success=False, message=f"Ultra thinking failed: {e!s}")

    @mcp.tool(
        name="learning_loop",
        description=(
            "Reflect on a thinking session and extract learnings."
            " Records the strategy used, outcome tags, reward signal,"
            " and insights for future self-improvement."
        ),
    )
    async def learning_loop(
        session_id: str = Field(
            description="The session to record learnings for.",
        ),
        strategy_used: str = Field(
            description="Name of the thinking strategy that was used.",
        ),
        outcome_tags: list[str] = Field(
            description="Tags describing the outcome (e.g., 'success', 'partial', 'failed').",
        ),
        reward: float = Field(
            ge=-1,
            le=1,
            description="Reward signal from -1.0 (worst) to 1.0 (best).",
        ),
        insights: list[str] = Field(
            description="Key insights extracted from the thinking session.",
        ),
    ) -> LearningLoopResult:
        try:
            engine = _get_engine()
            return engine.record_learning(session_id, strategy_used, outcome_tags, reward, insights)
        except Exception as e:
            return LearningLoopResult(success=False, message=f"Learning loop failed: {e!s}")

    @mcp.tool(
        name="self_improve",
        description=(
            "Get recommended thinking strategies based on past performance."
            " Analyzes historical learning entries and returns the top strategies"
            " ranked by average reward."
        ),
    )
    async def self_improve(
        top_k: int = Field(
            default=5,
            ge=1,
            le=20,
            description="Number of top strategies to return.",
        ),
    ) -> SelfImproveResult:
        try:
            engine = _get_engine()
            recommendations = engine.get_strategy_recommendations(top_k)
            return SelfImproveResult(
                success=True,
                recommended_strategies=recommendations,
                total_learnings=len(engine._learnings),
            )
        except Exception as e:
            return SelfImproveResult(success=False, message=f"Self improve failed: {e!s}")

    @mcp.tool(
        name="reward_thinking",
        description=(
            "Provide a reinforcement signal for a thinking session."
            " Applies an additional reward to the most recent learning"
            " entry for the given session, updating strategy scores."
        ),
    )
    async def reward_thinking(
        session_id: str = Field(
            description="The session to apply the reward to.",
        ),
        reward: float = Field(
            ge=-1,
            le=1,
            description="Reward signal from -1.0 (worst) to 1.0 (best).",
        ),
    ) -> RewardResult:
        try:
            engine = _get_engine()
            return engine.apply_reward(session_id, reward)
        except Exception as e:
            return RewardResult(success=False, message=f"Reward failed: {e!s}")

    @mcp.tool(
        name="evidence_tracker",
        description=(
            "Attach typed, weighted evidence to ultra_thinking hypotheses."
            " Supports 'add' to attach new evidence and 'list' to query existing"
            " evidence. Evidence types: code_ref, data_point, external,"
            " assumption, test_result. Returns cumulative strength score."
            " Use effort_mode to control depth: low (skip type validation),"
            " medium (standard), high (full validation),"
            " ultra (full validation + auto-boost strength for code_ref/test_result)."
        ),
    )
    async def evidence_tracker(
        session_id: str = Field(
            description="The ultra_thinking session containing hypotheses.",
        ),
        hypothesis_index: int = Field(
            ge=0,
            description="Zero-based index of the hypothesis to attach evidence to.",
        ),
        action: str = Field(
            default="add",
            description="Action to perform: 'add' to attach evidence, 'list' to query.",
        ),
        evidence: str | None = Field(
            default=None,
            description="The evidence text. Required when action is 'add'.",
        ),
        evidence_type: str = Field(
            default="data_point",
            description=(
                "Type of evidence: 'code_ref', 'data_point', 'external',"
                " 'assumption', or 'test_result'."
            ),
        ),
        strength: float = Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description="Strength of this evidence (0.0 to 1.0).",
        ),
        effort_mode: str = Field(
            default="ultra",
            description="Effort level: 'low', 'medium', 'high', or 'ultra'.",
        ),
    ) -> EvidenceTrackerResult:
        try:
            if effort_mode not in VALID_EFFORT_MODES:
                return EvidenceTrackerResult(
                    success=False,
                    session_id=session_id,
                    effort_mode=effort_mode,
                    message=(
                        f"Invalid effort_mode '{effort_mode}'."
                        f" Must be one of: {', '.join(sorted(VALID_EFFORT_MODES))}"
                    ),
                )
            engine = _get_engine()
            if action == "list":
                return engine.get_evidence(
                    session_id, hypothesis_index, effort_mode=effort_mode,
                )
            if action == "add":
                if evidence is None:
                    return EvidenceTrackerResult(
                        success=False,
                        session_id=session_id,
                        effort_mode=effort_mode,
                        message="evidence text is required when action is 'add'",
                    )
                return engine.add_evidence(
                    session_id, hypothesis_index, evidence,
                    evidence_type, strength, effort_mode=effort_mode,
                )
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                effort_mode=effort_mode,
                message=f"Invalid action '{action}'. Must be 'add' or 'list'.",
            )
        except Exception as e:
            return EvidenceTrackerResult(
                success=False, message=f"Evidence tracker failed: {e!s}"
            )

    @mcp.tool(
        name="premortem",
        description=(
            "Structured pre-failure risk analysis."
            " Imagine a plan has failed, then work backwards to identify why."
            " Phases: 'describe_plan', 'imagine_failure', 'identify_causes',"
            " 'rank_risks', 'mitigate'."
            " Use effort_mode to control depth: low (quick risk list),"
            " medium (full 5-phase flow), high (exhaustive analysis),"
            " ultra (auto-rank at every phase + require all mitigations)."
        ),
    )
    async def premortem(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed (can be adjusted).",
        ),
        phase: str = Field(
            default="describe_plan",
            description=(
                "Premortem phase: 'describe_plan', 'imagine_failure',"
                " 'identify_causes', 'rank_risks', or 'mitigate'."
            ),
        ),
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        plan: str | None = Field(
            default=None,
            description="The plan description. Used in 'describe_plan' phase.",
        ),
        failure_scenario: str | None = Field(
            default=None,
            description="The imagined failure scenario. Used in 'imagine_failure' phase.",
        ),
        risk_description: str | None = Field(
            default=None,
            description="Description of a risk cause. Required in 'identify_causes' phase.",
        ),
        likelihood: float = Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description="Likelihood of this risk (0.0 to 1.0).",
        ),
        impact: float = Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description="Impact severity of this risk (0.0 to 1.0).",
        ),
        risk_index: int | None = Field(
            default=None,
            description="Index of risk to mitigate. Required in 'mitigate' phase.",
        ),
        mitigation: str | None = Field(
            default=None,
            description="Mitigation strategy. Used in 'mitigate' phase.",
        ),
        effort_mode: str = Field(
            default="ultra",
            description="Effort level: 'low', 'medium', 'high', or 'ultra'.",
        ),
    ) -> PremortemResult:
        try:
            if effort_mode not in VALID_EFFORT_MODES:
                return PremortemResult(
                    success=False,
                    effort_mode=effort_mode,
                    message=(
                        f"Invalid effort_mode '{effort_mode}'."
                        f" Must be one of: {', '.join(sorted(VALID_EFFORT_MODES))}"
                    ),
                )
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
            )
            return engine.process_premortem(
                sid, data,
                phase=phase, plan=plan,
                failure_scenario=failure_scenario,
                risk_description=risk_description,
                likelihood=likelihood, impact=impact,
                mitigation=mitigation, risk_index=risk_index,
                effort_mode=effort_mode,
            )
        except Exception as e:
            return PremortemResult(
                success=False, message=f"Premortem failed: {e!s}"
            )

    @mcp.tool(
        name="inversion_thinking",
        description=(
            "Instead of asking 'how to succeed', ask 'how to guarantee failure',"
            " then invert. Phases: 'define_goal', 'invert',"
            " 'list_failure_causes', 'rank_causes' (medium/high only),"
            " 'reinvert', 'action_plan'."
            " Use effort_mode: low (skip ranking, 3 phases),"
            " medium (full 6 phases), high (auto-populate action plan),"
            " ultra (auto-reinvert all causes + auto-populate everything)."
        ),
    )
    async def inversion_thinking(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed (can be adjusted).",
        ),
        phase: str = Field(
            default="define_goal",
            description=(
                "Phase: 'define_goal', 'invert', 'list_failure_causes',"
                " 'rank_causes', 'reinvert', or 'action_plan'."
            ),
        ),
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        goal: str | None = Field(
            default=None,
            description="The goal to achieve. Used in 'define_goal' phase.",
        ),
        inverted_goal: str | None = Field(
            default=None,
            description="The inverted goal statement. Used in 'invert' phase.",
        ),
        failure_cause: str | None = Field(
            default=None,
            description="A cause of failure. Required in 'list_failure_causes' phase.",
        ),
        severity: float = Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description="Severity of this failure cause (0.0 to 1.0).",
        ),
        cause_index: int | None = Field(
            default=None,
            description="Index of cause to reinvert. Required in 'reinvert' phase.",
        ),
        inverted_action: str | None = Field(
            default=None,
            description="The positive action derived from inverting a cause.",
        ),
        action_item: str | None = Field(
            default=None,
            description="An action item for the plan. Used in 'action_plan' phase.",
        ),
        effort_mode: str = Field(
            default="ultra",
            description="Effort level: 'low', 'medium', 'high', or 'ultra'.",
        ),
    ) -> InversionThinkingResult:
        try:
            if effort_mode not in VALID_EFFORT_MODES:
                return InversionThinkingResult(
                    success=False,
                    effort_mode=effort_mode,
                    message=(
                        f"Invalid effort_mode '{effort_mode}'."
                        f" Must be one of: {', '.join(sorted(VALID_EFFORT_MODES))}"
                    ),
                )
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
            )
            return engine.process_inversion(
                sid, data,
                phase=phase, goal=goal,
                inverted_goal=inverted_goal,
                failure_cause=failure_cause,
                severity=severity,
                inverted_action=inverted_action,
                cause_index=cause_index,
                action_item=action_item,
                effort_mode=effort_mode,
            )
        except Exception as e:
            return InversionThinkingResult(
                success=False, message=f"Inversion thinking failed: {e!s}"
            )

    @mcp.tool(
        name="effort_estimator",
        description=(
            "Three-point PERT estimation for tasks."
            " Provide optimistic, likely, and pessimistic estimates"
            " to get PERT weighted average, standard deviation,"
            " and confidence intervals."
            " Actions: 'add' a task estimate, 'summary' to view all,"
            " 'clear' to reset."
            " Use effort_mode: low (single-point estimate),"
            " medium (PERT + 68% CI), high (PERT + 68% + 95% CI),"
            " ultra (PERT + 68% + 95% + 99.7% CI + risk buffer)."
        ),
    )
    async def effort_estimator(
        session_id: str | None = Field(
            default=None,
            description="Session identifier. Auto-generated if not provided.",
        ),
        action: str = Field(
            default="add",
            description="Action: 'add', 'summary', or 'clear'.",
        ),
        task: str | None = Field(
            default=None,
            description="Task name. Required when action is 'add'.",
        ),
        optimistic: float = Field(
            default=0.0,
            ge=0.0,
            description="Optimistic (best-case) estimate.",
        ),
        likely: float = Field(
            default=0.0,
            ge=0.0,
            description="Most likely estimate.",
        ),
        pessimistic: float = Field(
            default=0.0,
            ge=0.0,
            description="Pessimistic (worst-case) estimate.",
        ),
        effort_mode: str = Field(
            default="ultra",
            description="Effort level: 'low', 'medium', 'high', or 'ultra'.",
        ),
    ) -> EffortEstimatorResult:
        try:
            if effort_mode not in VALID_EFFORT_MODES:
                return EffortEstimatorResult(
                    success=False,
                    effort_mode=effort_mode,
                    message=(
                        f"Invalid effort_mode '{effort_mode}'."
                        f" Must be one of: {', '.join(sorted(VALID_EFFORT_MODES))}"
                    ),
                )
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            return engine.process_estimate(
                sid, action=action,
                task=task,
                optimistic=optimistic, likely=likely,
                pessimistic=pessimistic,
                effort_mode=effort_mode,
            )
        except Exception as e:
            return EffortEstimatorResult(
                success=False, message=f"Effort estimator failed: {e!s}"
            )

    @mcp.tool(
        name="plan_optimizer",
        description=(
            "Structured plan optimization tool."
            " Analyzes any plan (implementation, architecture, refactoring,"
            " bug fix) across 8 quality dimensions, auto-detects"
            " anti-patterns, supports 3 variant generation with"
            " comparison matrix scoring, and recommends the best approach."
            "\n\nPhases:"
            "\n1. 'submit_plan' — Submit plan text + context."
            "   Auto-detects anti-patterns."
            "\n2. 'analyze' — Score plan across dimensions"
            "   (clarity, completeness, correctness, risk, simplicity,"
            "   testability, edge_cases, actionability)."
            "   Call once per dimension with score 0-10."
            "\n3. 'detect_anti_patterns' — Re-run anti-pattern"
            "   detection (after plan edits)."
            "\n4. 'add_variant' — Add an alternative plan variant"
            "   (A=Minimal, B=Robust, C=Optimal Architecture)."
            "\n5. 'score_variant' — Score a variant across dimensions."
            "   Call once per dimension per variant."
            "\n6. 'recommend' — Submit final recommendation."
            "   Returns full comparison matrix."
            "\n\nUse effort_mode: low (just submit+analyze, skip variants),"
            " medium (full 6-phase flow),"
            " high (full flow + detailed anti-pattern analysis),"
            " ultra (auto-score missing dimensions + require variants for recommend)."
        ),
    )
    async def plan_optimizer(
        thought: str = Field(
            description="The current thinking step content.",
        ),
        next_thought_needed: bool = Field(
            description="Whether another thought step is needed.",
        ),
        thought_number: int = Field(
            ge=1,
            description="Current thought number in the sequence.",
        ),
        total_thoughts: int = Field(
            ge=1,
            description="Estimated total thoughts needed.",
        ),
        phase: str = Field(
            default="submit_plan",
            description=(
                "Phase: 'submit_plan', 'analyze',"
                " 'detect_anti_patterns', 'add_variant',"
                " 'score_variant', or 'recommend'."
            ),
        ),
        session_id: str | None = Field(
            default=None,
            description=(
                "Session identifier."
                " Auto-generated if not provided."
            ),
        ),
        plan_text: str | None = Field(
            default=None,
            description=(
                "The full plan text to optimize."
                " Required in 'submit_plan' phase."
            ),
        ),
        plan_context: str | None = Field(
            default=None,
            description=(
                "Context about what the plan is for."
                " E.g. 'Implementing user authentication'"
            ),
        ),
        dimension: str | None = Field(
            default=None,
            description=(
                "Dimension to score: clarity, completeness,"
                " correctness, risk, simplicity, testability,"
                " edge_cases, actionability."
                " Used in 'analyze' and 'score_variant' phases."
            ),
        ),
        score: float = Field(
            default=0.0,
            ge=0.0,
            le=10.0,
            description="Score for the dimension (0.0-10.0).",
        ),
        issue: str | None = Field(
            default=None,
            description=(
                "An issue found during analysis."
                " Used in 'analyze' phase."
            ),
        ),
        variant_label: str | None = Field(
            default=None,
            description=(
                "Variant label: 'A', 'B', or 'C'."
                " Used in 'add_variant' and 'score_variant'."
            ),
        ),
        variant_name: str | None = Field(
            default=None,
            description=(
                "Variant name, e.g. 'Minimal & Pragmatic'."
                " Used in 'add_variant'."
            ),
        ),
        variant_summary: str | None = Field(
            default=None,
            description="Brief approach summary for the variant.",
        ),
        variant_approach: str | None = Field(
            default=None,
            description="Full variant approach text.",
        ),
        variant_pros: list[str] | None = Field(
            default=None,
            description="List of pros for this variant.",
        ),
        variant_cons: list[str] | None = Field(
            default=None,
            description="List of cons for this variant.",
        ),
        variant_risk_level: str = Field(
            default="medium",
            description="Risk level: 'low', 'medium', 'high'.",
        ),
        variant_complexity: str = Field(
            default="medium",
            description="Complexity: 'low', 'medium', 'high'.",
        ),
        recommendation: str | None = Field(
            default=None,
            description=(
                "Final recommendation text."
                " Used in 'recommend' phase."
            ),
        ),
        winner_label: str | None = Field(
            default=None,
            description=(
                "Label of the winning variant."
                " Auto-selected if not provided."
            ),
        ),
        effort_mode: str = Field(
            default="ultra",
            description="Effort level: 'low', 'medium', 'high', or 'ultra'.",
        ),
    ) -> PlanOptimizerResult:
        try:
            if effort_mode not in VALID_EFFORT_MODES:
                return PlanOptimizerResult(
                    success=False,
                    effort_mode=effort_mode,
                    message=(
                        f"Invalid effort_mode '{effort_mode}'."
                        f" Must be one of: {', '.join(sorted(VALID_EFFORT_MODES))}"
                    ),
                )
            engine = _get_engine()
            sid = session_id or str(uuid.uuid4())
            data = ThoughtData(
                thought=thought,
                thought_number=thought_number,
                total_thoughts=total_thoughts,
                next_thought_needed=next_thought_needed,
            )
            return engine.process_plan_optimizer(
                sid, data,
                phase=phase,
                plan_text=plan_text,
                plan_context=plan_context,
                dimension=dimension,
                score=score,
                issue=issue,
                variant_label=variant_label,
                variant_name=variant_name,
                variant_summary=variant_summary,
                variant_approach=variant_approach,
                variant_pros=variant_pros,
                variant_cons=variant_cons,
                variant_risk_level=variant_risk_level,
                variant_complexity=variant_complexity,
                recommendation=recommendation,
                winner_label=winner_label,
                effort_mode=effort_mode,
            )
        except Exception as e:
            return PlanOptimizerResult(
                success=False,
                message=f"Plan optimizer failed: {e!s}",
            )
