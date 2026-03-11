"""Advanced thinking tools for the cocoindex-code MCP server.

Provides sequential_thinking, extended_thinking, ultra_thinking, learning_loop,
self_improve, and reward_thinking tools for structured reasoning, hypothesis
generation, and self-improving thought strategies.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .config import config

THINKING_MEMORY_FILE = "thinking_memory.jsonl"
MAX_THOUGHTS_PER_SESSION = 200
MAX_SESSIONS_STORED = 500
MAX_STRATEGIES = 100


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


# --- Shared constants ---

VALID_EFFORT_MODES: frozenset[str] = frozenset({"low", "medium", "high"})

VALID_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"code_ref", "data_point", "external", "assumption", "test_result"}
)

VALID_PREMORTEM_PHASES: frozenset[str] = frozenset(
    {"describe_plan", "imagine_failure", "identify_causes", "rank_risks", "mitigate"}
)

VALID_INVERSION_PHASES: frozenset[str] = frozenset(
    {"define_goal", "invert", "list_failure_causes", "rank_causes", "reinvert", "action_plan"}
)


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

PERT_WEIGHT = 4.0  # Standard PERT weighting for "most likely"


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
    effort_mode: str = "medium"
    message: str | None = None


class ThinkingEngine:
    def __init__(self, memory_dir: Path) -> None:
        self._memory_dir = memory_dir
        self._memory_file = memory_dir / THINKING_MEMORY_FILE
        self._sessions: dict[str, list[ThoughtData]] = {}
        self._branches: dict[str, dict[str, list[ThoughtData]]] = {}
        self._learnings: list[LearningEntry] = []
        self._strategy_scores: dict[str, StrategyScore] = {}
        self._hypotheses: dict[str, list[str]] = {}
        self._evidence: dict[str, dict[int, list[EvidenceItem]]] = {}
        self._premortems: dict[str, PremortemSession] = {}
        self._inversions: dict[str, InversionSession] = {}
        self._estimators: dict[str, EstimatorSession] = {}
        self._load_memory()

    @property
    def _memory_path(self) -> Path:
        return self._memory_file

    def _load_memory(self) -> None:
        try:
            with open(self._memory_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    entry_type = entry.get("type")
                    if entry_type == "learning":
                        self._learnings.append(LearningEntry(**entry["data"]))
                    elif entry_type == "strategy":
                        score = StrategyScore(**entry["data"])
                        self._strategy_scores[score.strategy] = score
        except FileNotFoundError:
            pass

    def _save_entry(self, entry: dict) -> None:
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _save_strategy(self, strategy: StrategyScore) -> None:
        self._save_entry({"type": "strategy", "data": strategy.model_dump()})

    def process_thought(self, session_id: str, data: ThoughtData) -> ThinkingResult:
        if session_id not in self._sessions:
            self._sessions[session_id] = []

        session_thoughts = self._sessions[session_id]

        if data.thought_number > data.total_thoughts:
            data = data.model_copy(update={"total_thoughts": data.thought_number})

        session_thoughts.append(data)

        branches: list[str] = []
        if data.branch_id is not None:
            if session_id not in self._branches:
                self._branches[session_id] = {}
            if data.branch_id not in self._branches[session_id]:
                self._branches[session_id][data.branch_id] = []
            self._branches[session_id][data.branch_id].append(data)
            branches = list(self._branches[session_id].keys())
        elif session_id in self._branches:
            branches = list(self._branches[session_id].keys())

        return ThinkingResult(
            success=True,
            session_id=session_id,
            thought_number=data.thought_number,
            total_thoughts=data.total_thoughts,
            next_thought_needed=data.next_thought_needed,
            branches=branches,
            thought_history_length=len(session_thoughts),
        )

    def process_extended_thought(
        self,
        session_id: str,
        data: ThoughtData,
        depth_level: str = "deep",
        checkpoint_interval: int = 5,
    ) -> ExtendedThinkingResult:
        if session_id not in self._sessions:
            self._sessions[session_id] = []

        session_thoughts = self._sessions[session_id]

        if data.thought_number > data.total_thoughts:
            data = data.model_copy(update={"total_thoughts": data.thought_number})

        session_thoughts.append(data)

        branches: list[str] = []
        if data.branch_id is not None:
            if session_id not in self._branches:
                self._branches[session_id] = {}
            if data.branch_id not in self._branches[session_id]:
                self._branches[session_id][data.branch_id] = []
            self._branches[session_id][data.branch_id].append(data)
            branches = list(self._branches[session_id].keys())
        elif session_id in self._branches:
            branches = list(self._branches[session_id].keys())

        checkpoint_summary = ""
        steps_since_checkpoint = data.thought_number % checkpoint_interval
        if steps_since_checkpoint == 0:
            checkpoint_summary = (
                f"Checkpoint at step {data.thought_number}: "
                f"{len(session_thoughts)} thoughts, {len(branches)} branches"
            )

        return ExtendedThinkingResult(
            success=True,
            session_id=session_id,
            thought_number=data.thought_number,
            total_thoughts=data.total_thoughts,
            next_thought_needed=data.next_thought_needed,
            branches=branches,
            thought_history_length=len(session_thoughts),
            depth_level=depth_level,
            checkpoint_summary=checkpoint_summary,
            steps_since_checkpoint=steps_since_checkpoint,
            checkpoint_interval=checkpoint_interval,
        )

    def process_ultra_thought(
        self,
        session_id: str,
        data: ThoughtData,
        phase: str = "explore",
        hypothesis: str | None = None,
        confidence: float = 0.0,
    ) -> UltraThinkingResult:
        if session_id not in self._sessions:
            self._sessions[session_id] = []

        session_thoughts = self._sessions[session_id]

        if data.thought_number > data.total_thoughts:
            data = data.model_copy(update={"total_thoughts": data.thought_number})

        session_thoughts.append(data)

        branches: list[str] = []
        if data.branch_id is not None:
            if session_id not in self._branches:
                self._branches[session_id] = {}
            if data.branch_id not in self._branches[session_id]:
                self._branches[session_id][data.branch_id] = []
            self._branches[session_id][data.branch_id].append(data)
            branches = list(self._branches[session_id].keys())
        elif session_id in self._branches:
            branches = list(self._branches[session_id].keys())

        if session_id not in self._hypotheses:
            self._hypotheses[session_id] = []

        verification_status = ""
        synthesis = ""

        if phase == "hypothesize" and hypothesis is not None:
            self._hypotheses[session_id].append(hypothesis)
        elif phase == "verify":
            if confidence >= 0.7:
                verification_status = "supported"
            elif confidence >= 0.4:
                verification_status = "partially_supported"
            else:
                verification_status = "unsupported"
        elif phase == "synthesize":
            all_hypotheses = self._hypotheses.get(session_id, [])
            if all_hypotheses:
                synthesis = "Synthesis of hypotheses: " + "; ".join(all_hypotheses)

        return UltraThinkingResult(
            success=True,
            session_id=session_id,
            thought_number=data.thought_number,
            total_thoughts=data.total_thoughts,
            next_thought_needed=data.next_thought_needed,
            branches=branches,
            thought_history_length=len(session_thoughts),
            phase=phase,
            hypotheses=list(self._hypotheses.get(session_id, [])),
            verification_status=verification_status,
            confidence=confidence,
            synthesis=synthesis,
        )

    def record_learning(
        self,
        session_id: str,
        strategy_used: str,
        outcome_tags: list[str],
        reward: float,
        insights: list[str],
    ) -> LearningLoopResult:
        thought_count = len(self._sessions.get(session_id, []))
        entry = LearningEntry(
            session_id=session_id,
            timestamp=time.time(),
            strategy_used=strategy_used,
            outcome_tags=outcome_tags,
            reward=reward,
            insights=insights,
            thought_count=thought_count,
        )
        self._learnings.append(entry)
        self._save_entry({"type": "learning", "data": entry.model_dump()})
        self._update_strategy_score(strategy_used, reward)

        return LearningLoopResult(
            success=True,
            session_id=session_id,
            learnings_extracted=1,
            insights=insights,
        )

    def get_strategy_recommendations(self, top_k: int = 5) -> list[StrategyScore]:
        sorted_strategies = sorted(
            self._strategy_scores.values(),
            key=lambda s: s.avg_reward,
            reverse=True,
        )
        return sorted_strategies[:top_k]

    def apply_reward(self, session_id: str, reward: float) -> RewardResult:
        matching = [entry for entry in self._learnings if entry.session_id == session_id]
        if not matching:
            return RewardResult(
                success=False,
                session_id=session_id,
                message=f"No learnings found for session {session_id}",
            )

        latest = matching[-1]
        latest.reward += reward
        self._update_strategy_score(latest.strategy_used, reward)
        self._save_entry({"type": "learning", "data": latest.model_dump()})

        cumulative = sum(entry.reward for entry in matching)

        return RewardResult(
            success=True,
            session_id=session_id,
            new_reward=reward,
            cumulative_reward=cumulative,
        )

    def _update_strategy_score(self, strategy: str, reward: float) -> None:
        if strategy not in self._strategy_scores:
            self._strategy_scores[strategy] = StrategyScore(strategy=strategy)

        score = self._strategy_scores[strategy]
        score.usage_count += 1
        score.total_reward += reward
        score.avg_reward = score.total_reward / score.usage_count
        score.last_used = time.time()

        self._save_strategy(score)

    # --- Evidence Tracker ---

    def add_evidence(
        self,
        session_id: str,
        hypothesis_index: int,
        text: str,
        evidence_type: str = "data_point",
        strength: float = 0.5,
        effort_mode: str = "medium",
    ) -> EvidenceTrackerResult:
        """Add evidence to a hypothesis in an ultra_thinking session."""
        hypotheses = self._hypotheses.get(session_id)
        if hypotheses is None:
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                effort_mode=effort_mode,
                message=f"No hypotheses found for session {session_id}",
            )
        if hypothesis_index < 0 or hypothesis_index >= len(hypotheses):
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                hypothesis_index=hypothesis_index,
                effort_mode=effort_mode,
                message=(
                    f"Hypothesis index {hypothesis_index} out of range"
                    f" (0..{len(hypotheses) - 1})"
                ),
            )
        # In low effort mode, skip type validation
        if effort_mode != "low" and evidence_type not in VALID_EVIDENCE_TYPES:
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                hypothesis_index=hypothesis_index,
                effort_mode=effort_mode,
                message=(
                    f"Invalid evidence_type '{evidence_type}'."
                    f" Must be one of: {', '.join(sorted(VALID_EVIDENCE_TYPES))}"
                ),
            )

        item = EvidenceItem(
            text=text,
            evidence_type=evidence_type if effort_mode != "low" else "data_point",
            strength=max(0.0, min(1.0, strength)),
            added_at=time.time(),
        )

        if session_id not in self._evidence:
            self._evidence[session_id] = {}
        if hypothesis_index not in self._evidence[session_id]:
            self._evidence[session_id][hypothesis_index] = []

        self._evidence[session_id][hypothesis_index].append(item)
        evidence_list = self._evidence[session_id][hypothesis_index]
        cumulative = sum(e.strength for e in evidence_list) / len(evidence_list)

        return EvidenceTrackerResult(
            success=True,
            session_id=session_id,
            hypothesis_index=hypothesis_index,
            hypothesis_text=hypotheses[hypothesis_index],
            evidence=list(evidence_list),
            total_evidence_count=len(evidence_list),
            cumulative_strength=cumulative,
            effort_mode=effort_mode,
        )

    def get_evidence(
        self,
        session_id: str,
        hypothesis_index: int,
        effort_mode: str = "medium",
    ) -> EvidenceTrackerResult:
        """List evidence for a hypothesis."""
        hypotheses = self._hypotheses.get(session_id)
        if hypotheses is None:
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                effort_mode=effort_mode,
                message=f"No hypotheses found for session {session_id}",
            )
        if hypothesis_index < 0 or hypothesis_index >= len(hypotheses):
            return EvidenceTrackerResult(
                success=False,
                session_id=session_id,
                hypothesis_index=hypothesis_index,
                effort_mode=effort_mode,
                message=(
                    f"Hypothesis index {hypothesis_index} out of range"
                    f" (0..{len(hypotheses) - 1})"
                ),
            )

        evidence_list = self._evidence.get(session_id, {}).get(hypothesis_index, [])
        cumulative = (
            sum(e.strength for e in evidence_list) / len(evidence_list)
            if evidence_list
            else 0.0
        )

        return EvidenceTrackerResult(
            success=True,
            session_id=session_id,
            hypothesis_index=hypothesis_index,
            hypothesis_text=hypotheses[hypothesis_index],
            evidence=list(evidence_list),
            total_evidence_count=len(evidence_list),
            cumulative_strength=cumulative,
            effort_mode=effort_mode,
        )

    # --- Premortem ---

    def process_premortem(
        self,
        session_id: str,
        data: ThoughtData,
        phase: str = "describe_plan",
        plan: str | None = None,
        failure_scenario: str | None = None,
        risk_description: str | None = None,
        likelihood: float = 0.5,
        impact: float = 0.5,
        mitigation: str | None = None,
        risk_index: int | None = None,
        effort_mode: str = "medium",
    ) -> PremortemResult:
        """Process a premortem thinking step."""
        if phase not in VALID_PREMORTEM_PHASES:
            return PremortemResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message=(
                    f"Invalid phase '{phase}'."
                    f" Must be one of: {', '.join(sorted(VALID_PREMORTEM_PHASES))}"
                ),
            )

        # Track thoughts in the main session store
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(data)

        # Initialize premortem session if needed
        if session_id not in self._premortems:
            self._premortems[session_id] = PremortemSession()

        pm = self._premortems[session_id]

        if phase == "describe_plan":
            if plan is not None:
                pm.plan = plan
            return PremortemResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_description=pm.plan,
                risks=list(pm.risks),
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "imagine_failure":
            if failure_scenario is not None:
                pm.failure_scenario = failure_scenario
            return PremortemResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_description=pm.plan,
                failure_scenario=pm.failure_scenario,
                risks=list(pm.risks),
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "identify_causes":
            if risk_description is None:
                return PremortemResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="risk_description is required for identify_causes phase",
                )
            clamped_likelihood = max(0.0, min(1.0, likelihood))
            clamped_impact = max(0.0, min(1.0, impact))
            risk = PremortemRisk(
                description=risk_description,
                likelihood=clamped_likelihood,
                impact=clamped_impact,
                risk_score=clamped_likelihood * clamped_impact,
            )
            pm.risks.append(risk)
            return PremortemResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_description=pm.plan,
                failure_scenario=pm.failure_scenario,
                risks=list(pm.risks),
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "rank_risks":
            ranked = sorted(pm.risks, key=lambda r: r.risk_score, reverse=True)
            return PremortemResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_description=pm.plan,
                failure_scenario=pm.failure_scenario,
                risks=list(pm.risks),
                ranked_risks=ranked,
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        # phase == "mitigate"
        if risk_index is None:
            return PremortemResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message="risk_index is required for mitigate phase",
            )
        if risk_index < 0 or risk_index >= len(pm.risks):
            return PremortemResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message=(
                    f"risk_index {risk_index} out of range"
                    f" (0..{len(pm.risks) - 1})"
                ),
            )
        if mitigation is not None:
            pm.risks[risk_index].mitigation = mitigation
        mitigations_count = sum(1 for r in pm.risks if r.mitigation)
        return PremortemResult(
            success=True,
            session_id=session_id,
            phase=phase,
            plan_description=pm.plan,
            failure_scenario=pm.failure_scenario,
            risks=list(pm.risks),
            mitigations_count=mitigations_count,
            thought_number=data.thought_number,
            total_thoughts=data.total_thoughts,
            next_thought_needed=data.next_thought_needed,
            effort_mode=effort_mode,
        )

    # --- Inversion Thinking ---

    def process_inversion(
        self,
        session_id: str,
        data: ThoughtData,
        phase: str = "define_goal",
        goal: str | None = None,
        inverted_goal: str | None = None,
        failure_cause: str | None = None,
        severity: float = 0.5,
        inverted_action: str | None = None,
        cause_index: int | None = None,
        action_item: str | None = None,
        effort_mode: str = "medium",
    ) -> InversionThinkingResult:
        """Process an inversion thinking step."""
        if phase not in VALID_INVERSION_PHASES:
            return InversionThinkingResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message=(
                    f"Invalid phase '{phase}'."
                    f" Must be one of: {', '.join(sorted(VALID_INVERSION_PHASES))}"
                ),
            )

        # Track thoughts
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(data)

        # Initialize session
        if session_id not in self._inversions:
            self._inversions[session_id] = InversionSession()

        inv = self._inversions[session_id]

        if phase == "define_goal":
            if goal is not None:
                inv.goal = goal
            return InversionThinkingResult(
                success=True,
                session_id=session_id,
                phase=phase,
                goal=inv.goal,
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "invert":
            if inverted_goal is not None:
                inv.inverted_goal = inverted_goal
            elif inv.goal and not inv.inverted_goal:
                # Auto-generate a basic inversion
                inv.inverted_goal = f"How to guarantee failure at: {inv.goal}"
            return InversionThinkingResult(
                success=True,
                session_id=session_id,
                phase=phase,
                goal=inv.goal,
                inverted_goal=inv.inverted_goal,
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "list_failure_causes":
            if failure_cause is None:
                return InversionThinkingResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="failure_cause is required for list_failure_causes phase",
                )
            clamped_severity = max(0.0, min(1.0, severity))
            cause = InversionCause(
                description=failure_cause,
                severity=clamped_severity,
            )
            inv.failure_causes.append(cause)
            return InversionThinkingResult(
                success=True,
                session_id=session_id,
                phase=phase,
                goal=inv.goal,
                inverted_goal=inv.inverted_goal,
                failure_causes=list(inv.failure_causes),
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "rank_causes":
            # Only available in medium/high effort
            if effort_mode == "low":
                return InversionThinkingResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="rank_causes phase is not available in low effort mode",
                )
            ranked = sorted(
                inv.failure_causes, key=lambda c: c.severity, reverse=True
            )
            return InversionThinkingResult(
                success=True,
                session_id=session_id,
                phase=phase,
                goal=inv.goal,
                inverted_goal=inv.inverted_goal,
                failure_causes=list(inv.failure_causes),
                ranked_causes=ranked,
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        if phase == "reinvert":
            if cause_index is None:
                return InversionThinkingResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="cause_index is required for reinvert phase",
                )
            if cause_index < 0 or cause_index >= len(inv.failure_causes):
                return InversionThinkingResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message=(
                        f"cause_index {cause_index} out of range"
                        f" (0..{len(inv.failure_causes) - 1})"
                    ),
                )
            if inverted_action is not None:
                inv.failure_causes[cause_index].inverted_action = inverted_action
            return InversionThinkingResult(
                success=True,
                session_id=session_id,
                phase=phase,
                goal=inv.goal,
                inverted_goal=inv.inverted_goal,
                failure_causes=list(inv.failure_causes),
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
            )

        # phase == "action_plan"
        if action_item is not None:
            inv.action_plan.append(action_item)
        # In high effort mode, auto-populate from reinverted causes if empty
        if effort_mode == "high" and not inv.action_plan:
            for cause in inv.failure_causes:
                if cause.inverted_action:
                    inv.action_plan.append(cause.inverted_action)
        return InversionThinkingResult(
            success=True,
            session_id=session_id,
            phase=phase,
            goal=inv.goal,
            inverted_goal=inv.inverted_goal,
            failure_causes=list(inv.failure_causes),
            action_plan=list(inv.action_plan),
            thought_number=data.thought_number,
            total_thoughts=data.total_thoughts,
            next_thought_needed=data.next_thought_needed,
            effort_mode=effort_mode,
        )

    # --- Effort Estimator ---

    @staticmethod
    def _compute_pert(
        optimistic: float, likely: float, pessimistic: float,
    ) -> EstimateItem:
        """Compute PERT estimate with confidence intervals."""
        pert = (optimistic + PERT_WEIGHT * likely + pessimistic) / 6.0
        std_dev = (pessimistic - optimistic) / 6.0
        return EstimateItem(
            task="",
            optimistic=optimistic,
            likely=likely,
            pessimistic=pessimistic,
            pert_estimate=pert,
            std_dev=std_dev,
            confidence_68_low=pert - std_dev,
            confidence_68_high=pert + std_dev,
            confidence_95_low=pert - 2 * std_dev,
            confidence_95_high=pert + 2 * std_dev,
        )

    def process_estimate(
        self,
        session_id: str,
        action: str = "add",
        task: str | None = None,
        optimistic: float = 0.0,
        likely: float = 0.0,
        pessimistic: float = 0.0,
        effort_mode: str = "medium",
    ) -> EffortEstimatorResult:
        """Process an effort estimation action."""
        if session_id not in self._estimators:
            self._estimators[session_id] = EstimatorSession()

        est = self._estimators[session_id]

        if action == "add":
            if task is None:
                return EffortEstimatorResult(
                    success=False,
                    session_id=session_id,
                    action=action,
                    effort_mode=effort_mode,
                    message="task name is required when action is 'add'",
                )
            if pessimistic < optimistic:
                return EffortEstimatorResult(
                    success=False,
                    session_id=session_id,
                    action=action,
                    effort_mode=effort_mode,
                    message="pessimistic must be >= optimistic",
                )
            if effort_mode == "low":
                # Low effort: use likely as single-point, skip PERT
                item = EstimateItem(
                    task=task,
                    optimistic=likely,
                    likely=likely,
                    pessimistic=likely,
                    pert_estimate=likely,
                )
            else:
                item = self._compute_pert(optimistic, likely, pessimistic)
                item.task = task
            est.estimates.append(item)

        elif action == "summary":
            pass  # Just return current state
        elif action == "clear":
            est.estimates.clear()
            return EffortEstimatorResult(
                success=True,
                session_id=session_id,
                action=action,
                effort_mode=effort_mode,
                message="Estimates cleared",
            )
        else:
            return EffortEstimatorResult(
                success=False,
                session_id=session_id,
                action=action,
                effort_mode=effort_mode,
                message=f"Invalid action '{action}'. Must be 'add', 'summary', or 'clear'.",
            )

        # Compute totals
        total_pert = sum(e.pert_estimate for e in est.estimates)
        total_std_dev = (
            sum(e.std_dev**2 for e in est.estimates) ** 0.5
            if effort_mode != "low"
            else 0.0
        )

        return EffortEstimatorResult(
            success=True,
            session_id=session_id,
            action=action,
            estimates=list(est.estimates),
            total_pert=total_pert,
            total_std_dev=total_std_dev,
            total_confidence_68_low=total_pert - total_std_dev if effort_mode != "low" else 0.0,
            total_confidence_68_high=total_pert + total_std_dev if effort_mode != "low" else 0.0,
            total_confidence_95_low=(
                total_pert - 2 * total_std_dev if effort_mode == "high" else 0.0
            ),
            total_confidence_95_high=(
                total_pert + 2 * total_std_dev if effort_mode == "high" else 0.0
            ),
            effort_mode=effort_mode,
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
            " medium (standard), high (full validation)."
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
            default="medium",
            description="Effort level: 'low', 'medium', or 'high'.",
        ),
    ) -> EvidenceTrackerResult:
        try:
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
            " medium (full 5-phase flow), high (exhaustive analysis)."
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
            default="medium",
            description="Effort level: 'low', 'medium', or 'high'.",
        ),
    ) -> PremortemResult:
        try:
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
            " medium (full 6 phases), high (auto-populate action plan)."
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
            default="medium",
            description="Effort level: 'low', 'medium', or 'high'.",
        ),
    ) -> InversionThinkingResult:
        try:
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
            " medium (PERT + 68% CI), high (PERT + 68% + 95% CI)."
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
            default="medium",
            description="Effort level: 'low', 'medium', or 'high'.",
        ),
    ) -> EffortEstimatorResult:
        try:
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
