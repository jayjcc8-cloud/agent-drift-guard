"""Explicit task, constraint, plan, and repository anchors."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from agent_drift.protocol.base import WireModel


class TaskAnchor(WireModel):
    goal: str = Field(min_length=1, max_length=32768)
    user_intent: str | None = Field(default=None, max_length=32768)
    non_goals: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()


class ConstraintAnchor(WireModel):
    hard_constraints: tuple[str, ...] = ()
    soft_constraints: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    forbidden_tools: frozenset[str] = frozenset()
    forbidden_command_patterns: tuple[str, ...] = ()

    @field_validator("forbidden_command_patterns")
    @classmethod
    def command_patterns_must_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            re.compile(pattern)
        return value


class PlanMilestone(WireModel):
    milestone_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=4096)
    status: str = Field(default="pending", pattern="^(pending|in_progress|completed|blocked)$")
    dependencies: tuple[str, ...] = ()


class PlanAnchor(WireModel):
    milestones: tuple[PlanMilestone, ...] = ()
    current_milestone: str | None = Field(default=None, max_length=256)


class RepoAnchor(WireModel):
    validation_command_patterns: tuple[str, ...] = (
        r"(?:^|\s)pytest(?:\s|$)",
        r"(?:^|(?:&&|\|\||;)\s*)(?:uv\s+run\s+)?(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?unittest(?:\s|$)",
        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
        r"(?:^|\s)cargo\s+test(?:\s|$)",
        r"(?:^|\s)go\s+test(?:\s|$)",
        r"(?:^|\s)dotnet\s+test(?:\s|$)",
    )

    @field_validator("validation_command_patterns")
    @classmethod
    def validation_patterns_must_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            re.compile(pattern)
        return value


class GuardAnchors(WireModel):
    task: TaskAnchor
    constraints: ConstraintAnchor = ConstraintAnchor()
    plan: PlanAnchor = PlanAnchor()
    repo: RepoAnchor = RepoAnchor()
