"""Agent construction.

One module builds every agent so that the model, the tool allow-list and the
structured-output contract are declared in exactly one place. If you are looking
for "what can this agent actually do", read the ``tools=`` argument — that list
is the capability surface, and anything not on it does not exist for that agent.
"""
from __future__ import annotations

from functools import lru_cache

from strands import Agent
from strands.models import BedrockModel
from strands.multiagent import Swarm

from ..config import settings
from ..correlation import find_candidate_cluster
from ..models import (
    CampaignResult,
    CorroborationResult,
    IntakeResult,
    ResponseResult,
    StageAssessment,
)
from ..prompts import (
    CAMPAIGN_PROMPT,
    CORROBORATION_PROMPT,
    INTAKE_PROMPT,
    ISOLATION_PROMPT,
    MONEY_RAIL_PROMPT,
    RESPONSE_PROMPT,
    SCRIPT_MATCHER_PROMPT,
)
from ..tools.enrichment import ENRICHMENT_TOOLS
from ..tools.store import lookup_prior_reports


@lru_cache(maxsize=1)
def model() -> BedrockModel:
    s = settings()
    return BedrockModel(model_id=s.model_id, region_name=s.region, temperature=0.2)


def _session_manager(session_id: str | None):
    """AgentCore-backed conversation memory for one agent role, or None.

    None (the default, and the only option when ``AGENTCORE_MEMORY_ID`` is
    unset) means the agent gets no memory beyond the single call it is used
    for — the same behaviour as before this existed. When a session_id is
    given and memory is configured, the agent's turns for that role persist in
    AgentCore Memory, scoped to this community, and are restored the next time
    a report is processed. This is conversational memory only: the structured
    community record store (cases, reports, campaigns) that correlation reads
    is unrelated and untouched by this — see README Limitations.
    """
    if session_id is None:
        return None
    from ..memory import build_session_manager  # noqa: PLC0415 — optional dependency path

    return build_session_manager(session_id)


def build_intake_agent(session_id: str | None = None) -> Agent:
    """No tools. Intake reads attacker-authored text, so it gets zero capability."""
    return Agent(
        model=model(),
        system_prompt=INTAKE_PROMPT,
        tools=[],
        structured_output_model=IntakeResult,
        name="intake",
        session_manager=_session_manager(session_id),
    )


def build_corroboration_agent(session_id: str | None = None) -> Agent:
    return Agent(
        model=model(),
        system_prompt=CORROBORATION_PROMPT,
        tools=[*ENRICHMENT_TOOLS, lookup_prior_reports],
        structured_output_model=CorroborationResult,
        name="corroboration",
        session_manager=_session_manager(session_id),
    )


def build_stage_swarm(session_id_prefix: str | None = None) -> Swarm:
    """Three assessors that disagree productively, with real handoffs."""
    common = dict(model=model(), tools=[], structured_output_model=StageAssessment)
    prefix = f"{session_id_prefix}-" if session_id_prefix else None
    script_matcher = Agent(
        system_prompt=SCRIPT_MATCHER_PROMPT, name="script_matcher",
        session_manager=_session_manager(f"{prefix}script_matcher" if prefix else None),
        **common,
    )
    money_rail = Agent(
        system_prompt=MONEY_RAIL_PROMPT, name="money_rail",
        session_manager=_session_manager(f"{prefix}money_rail" if prefix else None),
        **common,
    )
    isolation = Agent(
        system_prompt=ISOLATION_PROMPT, name="isolation",
        session_manager=_session_manager(f"{prefix}isolation" if prefix else None),
        **common,
    )
    return Swarm(
        [script_matcher, money_rail, isolation],
        entry_point=script_matcher,
        max_handoffs=6,
        max_iterations=8,
        execution_timeout=180.0,
        node_timeout=60.0,
        repetitive_handoff_detection_window=3,
        repetitive_handoff_min_unique_agents=2,
    )


def build_campaign_agent(session_id: str | None = None) -> Agent:
    return Agent(
        model=model(),
        system_prompt=CAMPAIGN_PROMPT,
        tools=[find_candidate_cluster],
        structured_output_model=CampaignResult,
        name="campaign",
        session_manager=_session_manager(session_id),
    )


def build_response_agent(session_id: str | None = None) -> Agent:
    """No send tools. Drafting only — sending is a gateway action, gated by policy."""
    return Agent(
        model=model(),
        system_prompt=RESPONSE_PROMPT,
        tools=[],
        structured_output_model=ResponseResult,
        name="response",
        session_manager=_session_manager(session_id),
    )
