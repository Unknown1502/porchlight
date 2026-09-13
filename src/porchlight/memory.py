"""Community-scoped memory.

The namespace is the community, not the individual. That single choice is what
makes Porchlight a Good Neighbor agent rather than a personal assistant: the
agent's long-term memory is the neighbourhood's shared picture of who is being
worked, and the value compounds with every report any resident files.

**What is actually wired, as of 2026-09-13.** Two different things share the
name "memory" here, and only one of them is backed by AgentCore:

- **The community record store** — the thing campaign correlation reads — is
  ``tools/store.py``, a JSON file, in every mode. Setting ``AGENTCORE_MEMORY_ID``
  does not change where records are read from or written to, and there is no
  plan to change that: correlation needs synchronous reads across every record
  in the community, which is a different access pattern from a conversational
  memory API, not a gap to be closed by pointing one at the other.
- **Agent conversation memory** — one Strands session per agent role
  (``intake``, ``corroboration``, the three stage-swarm assessors, ``campaign``,
  ``response``), scoped per community, via ``build_session_manager`` and wired
  into every builder in ``agents/factory.py``. This *is* backed by a real
  AgentCore Memory resource
  (``PorchlightCommunityMemory-csMZJnAAJD``, ``us-west-2``, `ACTIVE`, two
  strategies: semantic fact extraction and session summarisation). Verified
  live: a real agent call was made with a session manager attached, and
  ``list_events`` on the memory resource independently confirmed the turn was
  persisted — this is not just "the client didn't raise."

Two bugs were found only by doing that: ``community_actor_id`` returned
``"coalition::{id}"`` (a double colon), which AgentCore's actor-id pattern
rejects outright, and the session ids `agents/factory.py` built used ``:`` as a
separator, which the session-id pattern also rejects (only alnum/``-``/``_``).
Both are fixed; both were invisible until a session manager was actually
constructed against the live service, since no test does that.
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

    Single colon, not the double colon this returned before 2026-09-13. Verified
    live against ``ListEvents``: AgentCore rejects an actor id containing ``::``
    — it requires ``segment(:segment)*``, and a doubled colon produces an empty
    segment. Caught only once a session manager was actually constructed against
    the real service; nothing offline exercises this path.
    """
    return f"coalition:{community_id}"


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
