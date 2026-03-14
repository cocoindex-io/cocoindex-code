"""ThinkingEngine — core logic for thinking tools subsystem."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .thinking_models import (
    _MISSING_CONCERN_CHECKS,
    _VAGUE_PATTERNS,
    PERT_WEIGHT,
    PLAN_DIMENSIONS,
    THINKING_MEMORY_FILE,
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
    StrategyScore,
    ThinkingResult,
    ThoughtData,
    UltraThinkingResult,
)


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
        self._plan_optimizers: dict[str, PlanOptimizerSession] = {}
        self._load_memory()

    @property
    def _memory_path(self) -> Path:
        return self._memory_file

    def _load_memory(self) -> None:
        """Load thinking memory from JSONL, compacting if needed."""
        raw_line_count = 0
        try:
            with open(self._memory_file, encoding="utf-8") as f:
                for line in f:
                    raw_line_count += 1
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
            return

        # Compact if raw lines significantly exceed deduplicated count
        dedup_count = len(self._learnings) + len(self._strategy_scores)
        if raw_line_count > max(dedup_count * 2, 20):
            self._compact_memory()

    def _compact_memory(self) -> None:
        """Rewrite the JSONL file with only deduplicated entries."""
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)
        compact_path = self._memory_file.with_suffix(".jsonl.tmp")
        with open(compact_path, "w", encoding="utf-8") as f:
            for entry in self._learnings:
                f.write(json.dumps({"type": "learning", "data": entry.model_dump()}) + "\n")
            for score in self._strategy_scores.values():
                f.write(json.dumps({"type": "strategy", "data": score.model_dump()}) + "\n")
        compact_path.replace(self._memory_file)

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

        clamped_strength = max(0.0, min(1.0, strength))
        # Ultra mode: auto-boost strength for strongest evidence types
        if effort_mode == "ultra" and evidence_type in ("code_ref", "test_result"):
            clamped_strength = max(clamped_strength, 0.9)
        item = EvidenceItem(
            text=text,
            evidence_type=evidence_type if effort_mode != "low" else "data_point",
            strength=clamped_strength,
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
            # Ultra mode: auto-rank risks at every phase
            ranked = (
                sorted(pm.risks, key=lambda r: r.risk_score, reverse=True)
                if effort_mode == "ultra" else []
            )
            return PremortemResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_description=pm.plan,
                failure_scenario=pm.failure_scenario,
                risks=list(pm.risks),
                ranked_risks=ranked if ranked else [],
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
        # Ultra mode: warn if not all risks are mitigated
        ultra_message = None
        if effort_mode == "ultra" and mitigations_count < len(pm.risks):
            unmitigated = len(pm.risks) - mitigations_count
            ultra_message = (
                f"{unmitigated} risk(s) still lack mitigations."
                " Ultra mode requires all risks to be mitigated."
            )
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
            message=ultra_message,
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
        # Ultra mode: auto-reinvert ALL causes that lack inverted_actions,
        # then auto-populate action plan from ALL of them
        if effort_mode == "ultra":
            for cause in inv.failure_causes:
                if not cause.inverted_action:
                    cause.inverted_action = (
                        f"Prevent: {cause.description}"
                    )
            if not inv.action_plan:
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
            confidence_99_low=pert - 3 * std_dev,
            confidence_99_high=pert + 3 * std_dev,
            risk_buffer=pessimistic * 1.5,
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

        is_advanced = effort_mode in ("high", "ultra")
        return EffortEstimatorResult(
            success=True,
            session_id=session_id,
            action=action,
            estimates=list(est.estimates),
            total_pert=total_pert,
            total_std_dev=total_std_dev,
            total_confidence_68_low=(
                total_pert - total_std_dev
                if effort_mode != "low" else 0.0
            ),
            total_confidence_68_high=(
                total_pert + total_std_dev
                if effort_mode != "low" else 0.0
            ),
            total_confidence_95_low=(
                total_pert - 2 * total_std_dev
                if is_advanced else 0.0
            ),
            total_confidence_95_high=(
                total_pert + 2 * total_std_dev
                if is_advanced else 0.0
            ),
            total_confidence_99_low=(
                total_pert - 3 * total_std_dev
                if effort_mode == "ultra" else 0.0
            ),
            total_confidence_99_high=(
                total_pert + 3 * total_std_dev
                if effort_mode == "ultra" else 0.0
            ),
            total_risk_buffer=(
                sum(e.risk_buffer for e in est.estimates)
                if effort_mode == "ultra" else 0.0
            ),
            effort_mode=effort_mode,
        )

    # --- Plan Optimizer ---

    @staticmethod
    def _detect_anti_patterns(plan_text: str) -> list[PlanAntiPattern]:
        """Detect anti-patterns in a plan using regex heuristics."""

        results: list[PlanAntiPattern] = []
        plan_lower = plan_text.lower()
        lines = plan_text.splitlines()

        # 1. Vague language detection
        for pattern in _VAGUE_PATTERNS:
            for m in re.finditer(pattern, plan_lower):
                snippet = plan_lower[
                    max(0, m.start() - 20):m.end() + 20
                ].strip()
                results.append(PlanAntiPattern(
                    pattern_type="vague_language",
                    description=f"Vague language detected: "
                    f"'{m.group()}' in '...{snippet}...'",
                    severity="medium",
                    location=f"char {m.start()}",
                ))

        # 2. Missing concern checks
        for concern, keywords in _MISSING_CONCERN_CHECKS.items():
            found = any(kw in plan_lower for kw in keywords)
            if not found:
                sev = "high" if concern in (
                    "testing", "error_handling",
                ) else "medium"
                results.append(PlanAntiPattern(
                    pattern_type=f"missing_{concern}",
                    description=(
                        f"Plan does not mention {concern}."
                        f" Consider adding a step for:"
                        f" {', '.join(keywords)}"
                    ),
                    severity=sev,
                ))

        # 3. God-step detection (any single line > 500 chars)
        for i, line in enumerate(lines):
            if len(line.strip()) > 500:
                results.append(PlanAntiPattern(
                    pattern_type="god_step",
                    description=(
                        f"Step at line {i + 1} is very long"
                        f" ({len(line.strip())} chars)."
                        " Consider breaking into smaller steps."
                    ),
                    severity="high",
                    location=f"line {i + 1}",
                ))

        # 4. No structure (no numbered steps, bullets, or headers)
        has_structure = bool(re.search(
            r"^\s*(?:\d+[.)\-]|[-*•]|#{1,3}\s)",
            plan_text,
            re.MULTILINE,
        ))
        if not has_structure and len(lines) > 3:
            results.append(PlanAntiPattern(
                pattern_type="no_structure",
                description=(
                    "Plan lacks numbered steps, bullet points,"
                    " or section headers. Add structure."
                ),
                severity="medium",
            ))

        # 5. TODO/TBD markers
        for m in re.finditer(
            r"\b(TODO|TBD|FIXME|HACK|XXX)\b", plan_text,
        ):
            results.append(PlanAntiPattern(
                pattern_type="todo_marker",
                description=(
                    f"Unresolved marker: '{m.group()}'"
                ),
                severity="high",
                location=f"char {m.start()}",
            ))

        return results

    @staticmethod
    def _compute_plan_health(
        analysis_scores: dict[str, float],
        anti_pattern_count: int,
    ) -> float:
        """Compute plan health score 0-100."""
        if not analysis_scores:
            return 0.0
        # Base: average of dimension scores scaled to 100
        avg = sum(analysis_scores.values()) / len(analysis_scores)
        base = (avg / 10.0) * 100.0
        # Penalty: -5 per anti-pattern, floor at 0
        penalty = anti_pattern_count * 5
        return max(0.0, round(base - penalty, 1))

    @staticmethod
    def _build_comparison_matrix(
        variants: list[PlanVariant],
    ) -> dict[str, dict[str, float]]:
        """Build comparison matrix: dimension -> {label: score}."""
        matrix: dict[str, dict[str, float]] = {}
        for dim in PLAN_DIMENSIONS:
            matrix[dim] = {}
            for var in variants:
                matrix[dim][var.label] = var.scores.get(dim, 0.0)
        # Add totals row
        matrix["TOTAL"] = {
            var.label: var.total for var in variants
        }
        return matrix

    def process_plan_optimizer(
        self,
        session_id: str,
        data: ThoughtData,
        phase: str = "submit_plan",
        plan_text: str | None = None,
        plan_context: str | None = None,
        dimension: str | None = None,
        score: float = 0.0,
        issue: str | None = None,
        variant_label: str | None = None,
        variant_name: str | None = None,
        variant_summary: str | None = None,
        variant_approach: str | None = None,
        variant_pros: list[str] | None = None,
        variant_cons: list[str] | None = None,
        variant_risk_level: str = "medium",
        variant_complexity: str = "medium",
        recommendation: str | None = None,
        winner_label: str | None = None,
        effort_mode: str = "medium",
    ) -> PlanOptimizerResult:
        """Process a plan_optimizer phase."""
        if phase not in VALID_PLAN_OPTIMIZER_PHASES:
            return PlanOptimizerResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message=(
                    f"Invalid phase '{phase}'. Must be one of: "
                    f"{', '.join(sorted(VALID_PLAN_OPTIMIZER_PHASES))}"
                ),
            )

        # Track thoughts
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(data)

        # Init session
        if session_id not in self._plan_optimizers:
            self._plan_optimizers[session_id] = (
                PlanOptimizerSession()
            )
        po = self._plan_optimizers[session_id]

        def _result(**kwargs: object) -> PlanOptimizerResult:
            """Build result with common fields."""
            return PlanOptimizerResult(
                success=True,
                session_id=session_id,
                phase=phase,
                plan_text=po.plan_text,
                plan_context=po.plan_context,
                analysis_scores=dict(po.analysis_scores),
                analysis_issues=list(po.analysis_issues),
                anti_patterns=list(po.anti_patterns),
                anti_pattern_count=len(po.anti_patterns),
                plan_health_score=self._compute_plan_health(
                    po.analysis_scores,
                    len(po.anti_patterns),
                ),
                variants=list(po.variants),
                comparison_matrix=(
                    self._build_comparison_matrix(po.variants)
                    if po.variants else {}
                ),
                recommendation=po.recommendation,
                winner_label=po.winner_label,
                thought_number=data.thought_number,
                total_thoughts=data.total_thoughts,
                next_thought_needed=data.next_thought_needed,
                effort_mode=effort_mode,
                **kwargs,
            )

        # --- Phase: submit_plan ---
        if phase == "submit_plan":
            if not plan_text:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="plan_text is required for "
                    "submit_plan phase",
                )
            po.plan_text = plan_text
            if plan_context:
                po.plan_context = plan_context
            # Auto-detect anti-patterns on submit
            po.anti_patterns = self._detect_anti_patterns(
                plan_text,
            )
            return _result()

        # --- Phase: analyze ---
        if phase == "analyze":
            if dimension is not None:
                dim = dimension.lower()
                if dim not in PLAN_DIMENSIONS:
                    return PlanOptimizerResult(
                        success=False,
                        session_id=session_id,
                        phase=phase,
                        effort_mode=effort_mode,
                        message=(
                            f"Invalid dimension '{dimension}'."
                            f" Must be one of: "
                            f"{', '.join(PLAN_DIMENSIONS)}"
                        ),
                    )
                clamped = max(0.0, min(10.0, score))
                po.analysis_scores[dim] = clamped
            if issue:
                po.analysis_issues.append(issue)
            return _result()

        # --- Phase: detect_anti_patterns ---
        if phase == "detect_anti_patterns":
            # Re-run detection (useful after plan edits)
            po.anti_patterns = self._detect_anti_patterns(
                po.plan_text,
            )
            return _result()

        # --- Phase: add_variant ---
        if phase == "add_variant":
            if not variant_label:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="variant_label is required "
                    "(e.g. 'A', 'B', 'C')",
                )
            if not variant_name:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="variant_name is required",
                )
            # Check duplicate label
            existing = [
                v for v in po.variants
                if v.label == variant_label
            ]
            if existing:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message=(
                        f"Variant '{variant_label}' already exists."
                        " Use score_variant to update scores."
                    ),
                )
            variant = PlanVariant(
                label=variant_label,
                name=variant_name or "",
                summary=variant_summary or "",
                approach=variant_approach or "",
                pros=variant_pros or [],
                cons=variant_cons or [],
                risk_level=variant_risk_level,
                complexity=variant_complexity,
            )
            po.variants.append(variant)
            return _result()

        # --- Phase: score_variant ---
        if phase == "score_variant":
            if not variant_label:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message="variant_label is required",
                )
            target = None
            for v in po.variants:
                if v.label == variant_label:
                    target = v
                    break
            if target is None:
                return PlanOptimizerResult(
                    success=False,
                    session_id=session_id,
                    phase=phase,
                    effort_mode=effort_mode,
                    message=(
                        f"Variant '{variant_label}' not found."
                        " Call add_variant first."
                    ),
                )
            if dimension is not None:
                dim = dimension.lower()
                if dim not in PLAN_DIMENSIONS:
                    return PlanOptimizerResult(
                        success=False,
                        session_id=session_id,
                        phase=phase,
                        effort_mode=effort_mode,
                        message=(
                            f"Invalid dimension '{dimension}'."
                            f" Must be one of: "
                            f"{', '.join(PLAN_DIMENSIONS)}"
                        ),
                    )
                clamped = max(0.0, min(10.0, score))
                target.scores[dim] = clamped
                target.total = sum(target.scores.values())
            return _result()

        # --- Phase: recommend ---
        # phase == "recommend"
        # Ultra mode: block recommend if no variants added
        if effort_mode == "ultra" and not po.variants:
            return PlanOptimizerResult(
                success=False,
                session_id=session_id,
                phase=phase,
                effort_mode=effort_mode,
                message=(
                    "Ultra mode requires at least one variant"
                    " before recommending."
                    " Use add_variant first."
                ),
            )
        # Ultra mode: auto-score unscored dimensions as 0
        if effort_mode == "ultra":
            for dim in PLAN_DIMENSIONS:
                if dim not in po.analysis_scores:
                    po.analysis_scores[dim] = 0.0
            for var in po.variants:
                for dim in PLAN_DIMENSIONS:
                    if dim not in var.scores:
                        var.scores[dim] = 0.0
                var.total = sum(var.scores.values())
        if recommendation:
            po.recommendation = recommendation
        if winner_label:
            po.winner_label = winner_label
        # Auto-pick winner by highest total if not specified
        if not po.winner_label and po.variants:
            best = max(po.variants, key=lambda v: v.total)
            po.winner_label = best.label
        return _result()



