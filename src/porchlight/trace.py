"""Operational traces.

What a coordinator gets to see about a step: which tools ran, what evidence was
used, what came out, and what is still missing. Deliberately **not** the model's
reasoning.

That is a product decision, not a limitation. Exposing chain-of-thought would put
the model's private deliberation — including whatever a hostile report managed to
push into it — onto a screen a volunteer reads and a partner agency may receive.
A trace that says *"called domain_age_days on verify-kyc.example, registered 6
days ago, raised confidence to moderate"* is checkable by someone with no
interest in language models. A paragraph of reasoning is not, and invites the
reader to trust fluency.

So every field here is a fact about an action taken, and the schema is narrow
enough that free-form deliberation has nowhere to go.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """One tool invocation and what it returned. Arguments are recorded because
    'which URL did it check' is exactly the question an audit asks."""

    tool: str
    argument: str
    verdict: str = ""
    detail: str = ""
    source: str = ""
    fixture_backed: bool = False
    duration_ms: int = 0

    @property
    def label(self) -> str:
        badge = " (fixture)" if self.fixture_backed else ""
        return f"{self.tool}({self.argument}){badge} -> {self.verdict or 'n/a'}"


class TraceStep(BaseModel):
    """One node of the pipeline."""

    step: str
    mode: str = "offline-deterministic"     # or "bedrock"
    started_at: float = Field(default_factory=time.time)
    duration_ms: int = 0
    tools_called: list[ToolCall] = Field(default_factory=list)
    # Indicator keys / prior report ids the step actually consumed.
    evidence_used: list[str] = Field(default_factory=list)
    # What the step could not establish. This is the field that makes the
    # corroboration agent's job legible: it names the gap it went looking for.
    missing_evidence: list[str] = Field(default_factory=list)
    outcome: str = ""
    recommendation: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class Trace(BaseModel):
    """The whole run for one report."""

    report_id: str
    steps: list[TraceStep] = Field(default_factory=list)

    @property
    def total_ms(self) -> int:
        return sum(s.duration_ms for s in self.steps)

    @property
    def tool_calls(self) -> int:
        return sum(len(s.tools_called) for s in self.steps)

    def step(self, name: str) -> Optional[TraceStep]:
        return next((s for s in self.steps if s.step == name), None)

    def summary_lines(self) -> list[str]:
        """One line per step, for the CLI and the expandable UI panel."""
        out = []
        for s in self.steps:
            bits = [f"{s.step}: {s.outcome or '—'}"]
            if s.tools_called:
                bits.append("  tools: " + "; ".join(c.label for c in s.tools_called))
            if s.missing_evidence:
                bits.append("  missing: " + ", ".join(s.missing_evidence))
            if s.error:
                bits.append(f"  ERROR: {s.error}")
            out.extend(bits)
        return out


class TraceRecorder:
    """Collects steps for one report.

    Kept as an explicit object passed down the pipeline rather than a thread-local
    or a global, because the background worker runs reports concurrently with the
    server answering requests, and an ambient recorder would interleave two
    reports' steps into one trace.
    """

    def __init__(self, report_id: str, mode: str = "offline-deterministic") -> None:
        self.trace = Trace(report_id=report_id)
        self.mode = mode
        self._budget_used = 0

    @contextmanager
    def step(self, name: str) -> Iterator[TraceStep]:
        entry = TraceStep(step=name, mode=self.mode)
        started = time.perf_counter()
        self.trace.steps.append(entry)
        try:
            yield entry
        except Exception as exc:  # noqa: BLE001 — the trace must record failures too
            entry.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            entry.duration_ms = int((time.perf_counter() - started) * 1000)

    def record_tool(self, step: TraceStep, tool: str, argument: str,
                    result: dict[str, Any], duration_ms: int = 0) -> None:
        step.tools_called.append(ToolCall(
            tool=tool, argument=str(argument)[:120],
            verdict=str(result.get("verdict", "")),
            detail=str(result.get("detail", ""))[:200],
            source=str(result.get("source", "")),
            fixture_backed=bool(result.get("fixture_backed", False)),
            duration_ms=duration_ms,
        ))
        self._budget_used += 1

    def budget_exhausted(self, limit: int) -> bool:
        """Bound the work one report can cause.

        A report engineered to name forty URLs must not be able to spend forty
        tool calls, or a hostile input becomes a cost-and-latency attack on the
        queue.
        """
        return self._budget_used >= limit
