"""Shared schemas for InsightPilot's sequential LangGraph workflow."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: int
    question: str
    purpose: str


class SQLQuery(BaseModel):
    step_id: int
    sql: str
    explanation: str


class QueryResult(BaseModel):
    step_id: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class Finding(BaseModel):
    finding: str
    metric: str
    supporting_data: list[dict[str, Any]] = Field(default_factory=list)


class Insight(BaseModel):
    step_id: int
    findings: list[Finding]


class ChartSpec(BaseModel):
    step_id: int
    chart_type: Literal["bar", "line", "pie", "scatter", "table"]
    x: str | None = None
    y: str | None = None
    color: str | None = None
    title: str
    caption: str


class AgentState(TypedDict, total=False):
    question: str
    db_path: str
    schema: str
    plan: list[dict[str, Any]]
    current_step: int
    queries: list[dict[str, Any]]
    results: list[dict[str, Any]]
    retry_count: int
    validation_feedback: str | None
    insights: list[dict[str, Any]]
    chart_specs: list[dict[str, Any]]
    # In-memory Plotly Figure objects, keyed by SQL-result/insight step ID.
    figures: dict[int, Any]
    final_summary: str
    errors: list[str]
