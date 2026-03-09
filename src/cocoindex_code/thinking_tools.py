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


class ThinkingEngine:
    def __init__(self, memory_dir: Path) -> None:
        self._memory_dir = memory_dir
        self._memory_file = memory_dir / THINKING_MEMORY_FILE
        self._sessions: dict[str, list[ThoughtData]] = {}
        self._branches: dict[str, dict[str, list[ThoughtData]]] = {}
        self._learnings: list[LearningEntry] = []
        self._strategy_scores: dict[str, StrategyScore] = {}
        self._hypotheses: dict[str, list[str]] = {}
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
