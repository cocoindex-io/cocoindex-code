"""Pydantic models and constants for the thinking tools subsystem."""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Configuration constants ---

THINKING_MEMORY_FILE = "thinking_memory.jsonl"
MAX_THOUGHTS_PER_SESSION = 200
MAX_SESSIONS_STORED = 500
MAX_STRATEGIES = 100
PERT_WEIGHT = 4.0  # Standard PERT weighting for "most likely"


# --- Shared constants ---

VALID_EFFORT_MODES: frozenset[str] = frozenset({"low", "medium", "high", "ultra"})

VALID_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"code_ref", "data_point", "external", "assumption", "test_result"}
)

VALID_PREMORTEM_PHASES: frozenset[str] = frozenset(
    {"describe_plan", "imagine_failure", "identify_causes", "rank_risks", "mitigate"}
)

VALID_INVERSION_PHASES: frozenset[str] = frozenset(
    {"define_goal", "invert", "list_failure_causes", "rank_causes", "reinvert", "action_plan"}
)

VALID_PLAN_OPTIMIZER_PHASES: frozenset[str] = frozenset(
    {
        "submit_plan", "analyze", "detect_anti_patterns",
        "add_variant", "score_variant", "recommend",
    }
)

PLAN_DIMENSIONS: tuple[str, ...] = (
    "clarity", "completeness", "correctness", "risk",
    "simplicity", "testability", "edge_cases", "actionability",
)


# --- Anti-pattern detection patterns ---

_VAGUE_PATTERNS: list[str] = [
    r"\bmake it work\b",
    r"\bfix it\b",
    r"\bclean up\b",
    r"\bimprove\b(?!ment)",
    r"\bjust do\b",
    r"\bsomehow\b",
    r"\betc\.?\b",
    r"\bstuff\b",
    r"\bthings\b",
    r"\bhandle it\b",
    r"\bfigure out\b",
    r"\bwhatever\b",
]

_MISSING_CONCERN_CHECKS: dict[str, list[str]] = {
    "testing": ["test", "verify", "assert", "validate", "spec"],
    "error_handling": ["error", "exception", "fail", "catch", "handle"],
    "edge_cases": ["edge case", "corner case", "empty", "null", "none", "zero", "boundary"],
    "security": ["auth", "permission", "sanitize", "escape", "inject"],
    "performance": ["performance", "scale", "cache", "optimize", "latency", "throughput"],
}


# --- Core thought model ---


class ThoughtData(BaseModel):
    thought: str
    thought_number: int
    total_thoughts: int
    next_thought_needed: bool
    is_revision: bool = False
    revises_thought: int | None = None
    branch_from_thought: int | None = None
    branch_id: str | None = None
    needs_more_thoughts: bool = False


# --- Result models ---


class ThinkingResult(BaseModel):
    success: bool
    session_id: str = ""
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    branches: list[str] = Field(default_factory=list)
    thought_history_length: int = 0
    message: str | None = None


class ExtendedThinkingResult(BaseModel):
    success: bool
    session_id: str = ""
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    branches: list[str] = Field(default_factory=list)
    thought_history_length: int = 0
    message: str | None = None
    depth_level: str = "standard"
    checkpoint_summary: str = ""
    steps_since_checkpoint: int = 0
    checkpoint_interval: int = 0


class UltraThinkingResult(BaseModel):
    success: bool
    session_id: str = ""
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    branches: list[str] = Field(default_factory=list)
    thought_history_length: int = 0
    message: str | None = None
    depth_level: str = "standard"
    checkpoint_summary: str = ""
    steps_since_checkpoint: int = 0
    checkpoint_interval: int = 0
    phase: str = ""
    hypotheses: list[str] = Field(default_factory=list)
    verification_status: str = ""
    confidence: float = 0.0
    synthesis: str = ""


class LearningEntry(BaseModel):
    session_id: str
    timestamp: float
    strategy_used: str
    outcome_tags: list[str] = Field(default_factory=list)
    reward: float = 0.0
    insights: list[str] = Field(default_factory=list)
    thought_count: int = 0


class LearningLoopResult(BaseModel):
    success: bool
    session_id: str = ""
    learnings_extracted: int = 0
    insights: list[str] = Field(default_factory=list)
    message: str | None = None


class StrategyScore(BaseModel):
    strategy: str
    total_reward: float = 0.0
    usage_count: int = 0
    avg_reward: float = 0.0
    last_used: float = 0.0


class SelfImproveResult(BaseModel):
    success: bool
    recommended_strategies: list[StrategyScore] = Field(default_factory=list)
    total_learnings: int = 0
    message: str | None = None


class RewardResult(BaseModel):
    success: bool
    session_id: str = ""
    new_reward: float = 0.0
    cumulative_reward: float = 0.0
    message: str | None = None


# --- Evidence Tracker models ---


class EvidenceItem(BaseModel):
    """A single piece of evidence attached to a hypothesis."""

    text: str
    evidence_type: str = "data_point"
    strength: float = 0.5
    added_at: float = 0.0


class EvidenceTrackerResult(BaseModel):
    """Result from the evidence_tracker tool."""

    success: bool
    session_id: str = ""
    hypothesis_index: int = 0
    hypothesis_text: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    total_evidence_count: int = 0
    cumulative_strength: float = 0.0
    effort_mode: str = "medium"
    message: str | None = None


# --- Premortem models ---


class PremortemRisk(BaseModel):
    """A single risk identified during a premortem session."""

    description: str
    likelihood: float = 0.5
    impact: float = 0.5
    risk_score: float = 0.25
    mitigation: str = ""
    category: str = ""


class PremortemSession(BaseModel):
    """Internal state for a premortem session."""

    plan: str = ""
    failure_scenario: str = ""
    risks: list[PremortemRisk] = Field(default_factory=list)


class PremortemResult(BaseModel):
    """Result from the premortem tool."""

    success: bool
    session_id: str = ""
    phase: str = ""
    plan_description: str = ""
    failure_scenario: str = ""
    risks: list[PremortemRisk] = Field(default_factory=list)
    ranked_risks: list[PremortemRisk] = Field(default_factory=list)
    mitigations_count: int = 0
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    effort_mode: str = "medium"
    message: str | None = None


# --- Inversion Thinking models ---


class InversionCause(BaseModel):
    """A cause of failure identified via inversion."""

    description: str
    severity: float = 0.5
    inverted_action: str = ""


class InversionSession(BaseModel):
    """Internal state for an inversion thinking session."""

    goal: str = ""
    inverted_goal: str = ""
    failure_causes: list[InversionCause] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)


class InversionThinkingResult(BaseModel):
    """Result from the inversion_thinking tool."""

    success: bool
    session_id: str = ""
    phase: str = ""
    goal: str = ""
    inverted_goal: str = ""
    failure_causes: list[InversionCause] = Field(default_factory=list)
    ranked_causes: list[InversionCause] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    effort_mode: str = "medium"
    message: str | None = None


# --- Effort Estimator models ---


class EstimateItem(BaseModel):
    """A single task estimate."""

    task: str
    optimistic: float
    likely: float
    pessimistic: float
    pert_estimate: float = 0.0
    std_dev: float = 0.0
    confidence_68_low: float = 0.0
    confidence_68_high: float = 0.0
    confidence_95_low: float = 0.0
    confidence_95_high: float = 0.0
    confidence_99_low: float = 0.0
    confidence_99_high: float = 0.0
    risk_buffer: float = 0.0


class EstimatorSession(BaseModel):
    """Internal state for an effort estimator session."""

    estimates: list[EstimateItem] = Field(default_factory=list)


class EffortEstimatorResult(BaseModel):
    """Result from the effort_estimator tool."""

    success: bool
    session_id: str = ""
    action: str = ""
    estimates: list[EstimateItem] = Field(default_factory=list)
    total_pert: float = 0.0
    total_std_dev: float = 0.0
    total_confidence_68_low: float = 0.0
    total_confidence_68_high: float = 0.0
    total_confidence_95_low: float = 0.0
    total_confidence_95_high: float = 0.0
    total_confidence_99_low: float = 0.0
    total_confidence_99_high: float = 0.0
    total_risk_buffer: float = 0.0
    effort_mode: str = "medium"
    message: str | None = None


# --- Plan Optimizer models ---


class PlanAntiPattern(BaseModel):
    """An anti-pattern detected in a plan."""

    pattern_type: str = Field(
        description="Type: vague_language, missing_testing, "
        "missing_error_handling, missing_edge_cases, god_step, "
        "no_structure, todo_marker, missing_security, "
        "missing_performance"
    )
    description: str = Field(description="What was detected")
    severity: str = Field(
        default="medium",
        description="Severity: low, medium, high",
    )
    location: str = Field(
        default="",
        description="Where in the plan this was found",
    )


class PlanVariant(BaseModel):
    """A plan variant with scores."""

    label: str = Field(description="Variant label: A, B, or C")
    name: str = Field(
        description="Variant name, e.g. 'Minimal & Pragmatic'",
    )
    summary: str = Field(description="Brief approach summary")
    approach: str = Field(
        default="", description="Full variant approach text",
    )
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    risk_level: str = Field(default="medium")
    complexity: str = Field(default="medium")
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Dimension scores (0.0-10.0)",
    )
    total: float = Field(default=0.0, description="Sum of all scores")


class PlanOptimizerSession(BaseModel):
    """Internal state for a plan_optimizer session."""

    plan_text: str = ""
    plan_context: str = ""
    analysis_scores: dict[str, float] = Field(default_factory=dict)
    analysis_issues: list[str] = Field(default_factory=list)
    anti_patterns: list[PlanAntiPattern] = Field(default_factory=list)
    variants: list[PlanVariant] = Field(default_factory=list)
    recommendation: str = ""
    winner_label: str = ""


class PlanOptimizerResult(BaseModel):
    """Result from the plan_optimizer tool."""

    success: bool
    session_id: str = ""
    phase: str = ""
    plan_text: str = ""
    plan_context: str = ""
    analysis_scores: dict[str, float] = Field(default_factory=dict)
    analysis_issues: list[str] = Field(default_factory=list)
    anti_patterns: list[PlanAntiPattern] = Field(default_factory=list)
    anti_pattern_count: int = 0
    plan_health_score: float = Field(
        default=0.0,
        description="Overall plan health 0-100 based on analysis",
    )
    variants: list[PlanVariant] = Field(default_factory=list)
    comparison_matrix: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Dimension -> {variant_label: score}",
    )
    recommendation: str = ""
    winner_label: str = ""
    thought_number: int = 0
    total_thoughts: int = 0
    next_thought_needed: bool = True
    effort_mode: str = "medium"
    message: str | None = None
