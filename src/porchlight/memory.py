"""Community-scoped memory.

The namespace is the community, not the individual. That single choice is what
makes Porchlight a Good Neighbor agent rather than a personal assistant: the
agent's long-term memory is the neighbourhood's shared picture of who is being
worked, and the value compounds with every report any resident files.

**What is actually wired, as of this commit.** The community record store —
the thing campaign correlation reads — is ``tools/store.py``, a JSON file, in
every mode. It is not backed by AgentCore Memory, and setting
``AGENTCORE_MEMORY_ID`` does not change where records are read from or written
to. ``create_memory_resource`` below does create a real AgentCore Memory
resource, and ``build_session_manager`` returns a working Strands session
manager, but **nothing calls the latter**: no agent in ``agents/factory.py`` is
constructed with a ``session_manager``.

That is a gap, not a feature, and it is written here rather than in a footnote
because the shape of the module invites the opposite assumption. Wiring it up
means deciding what belongs in agent conversation memory versus the structured
record store, which are different things — the session manager carries turns,
and correlation needs rows. See "Limitations" in the README.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import settings

log = logging.getLogger(__name__)


def community_actor_id(community_id: str) -> str:
    """AgentCore Memory actor id.

    Note the deliberate shape: the *coalition* is the actor. Residents are never
    actors, so no resident ever gets a durable profile in this system.
    """
    return f"coalition::{community_id}"


def build_session_manager(session_id: str):
    """Return a Strands SessionManager backed by AgentCore Memory, or None."""
    s = settings()
    if not s.use_agentcore_memory:
        return None
    try:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )
    except ImportError:
        log.warning("bedrock-agentcore not installed; falling back to local store")
        return None

    cfg = AgentCoreMemoryConfig(
        memory_id=s.memory_id,
        session_id=session_id,
        actor_id=community_actor_id(s.community_id),
    )
    return AgentCoreMemorySessionManager(cfg, region_name=s.region)


def create_memory_resource(name: str = "PorchlightCommunityMemory") -> dict[str, Any]:
    """One-time setup: create the AgentCore Memory resource with a semantic strategy.

    Run via ``python -m porchlight.cli setup-memory``. Prints the memory id to put
    in ``AGENTCORE_MEMORY_ID``.
    """
    from bedrock_agentcore.memory import MemoryClient

    client = MemoryClient(region_name=settings().region)
    return client.create_memory_and_wait(
        name=name,
        description="Community-scoped scam campaign memory for a local elder-fraud coalition",
        strategies=[
            {"semanticMemoryStrategy": {"name": "CampaignFactExtractor"}},
            {"summaryMemoryStrategy": {"name": "CoalitionSessionSummarizer"}},
        ],
    )
