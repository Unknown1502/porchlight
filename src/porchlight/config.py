"""Runtime configuration and jurisdiction packs.

A jurisdiction is a config file, not a code fork. ``packs/in.yaml`` and
``packs/us.yaml`` carry the reporting endpoints, escalation contacts and script
library for a place. Switching packs is what makes the demo's "and here it is
running for another country" moment a thirty-second change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "packs"


@dataclass(frozen=True)
class Settings:
    model_id: str = os.getenv("PORCHLIGHT_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
    region: str = os.getenv("AWS_REGION", "us-west-2")
    offline: bool = os.getenv("PORCHLIGHT_OFFLINE", "1") == "1"
    pack_name: str = os.getenv("PORCHLIGHT_PACK", "in")
    community_id: str = os.getenv("PORCHLIGHT_COMMUNITY_ID", "demo-coalition")
    memory_id: str = os.getenv("AGENTCORE_MEMORY_ID", "")
    gateway_url: str = os.getenv("AGENTCORE_GATEWAY_URL", "")
    policy_engine_id: str = os.getenv("AGENTCORE_POLICY_ENGINE_ID", "")
    urlhaus_key: str = os.getenv("URLHAUS_AUTH_KEY", "")

    # Campaign correlation thresholds. Deliberately conservative: a false
    # "campaign" broadcast to a whole senior list is worse than a missed one.
    campaign_min_reports: int = 3
    campaign_min_distinct_reporters: int = 3
    campaign_window_days: int = 14

    @property
    def use_agentcore_memory(self) -> bool:
        return bool(self.memory_id)


@dataclass
class JurisdictionPack:
    """The local response adapter for one jurisdiction.

    The intelligence core - indicator extraction, correlation, the evidence
    model, the policy rules - contains no country. Everything a place changes
    lives in one of these files: where to report, who the partners are, which
    scripts and money rails are seen locally, which institutions get
    impersonated, what a plausible phone number looks like, and the currency.

    Adding a jurisdiction is adding a YAML file, not editing Python.
    """

    name: str
    display_name: str
    currency: str
    reporting: dict[str, Any] = field(default_factory=dict)
    partners: list[dict[str, Any]] = field(default_factory=list)
    script_library: list[dict[str, Any]] = field(default_factory=list)
    money_rails: list[str] = field(default_factory=list)
    notes: str = ""
    # Detection vocabulary. Merged ahead of the core's jurisdiction-neutral set,
    # so a local script wins over a generic one where both would match.
    script_patterns: list[dict[str, Any]] = field(default_factory=list)
    rail_patterns: list[dict[str, Any]] = field(default_factory=list)
    impersonated_entities: list[str] = field(default_factory=list)
    currency_patterns: list[dict[str, Any]] = field(default_factory=list)
    phone_shapes: list[dict[str, Any]] = field(default_factory=list)


@lru_cache(maxsize=8)
def load_pack(name: str) -> JurisdictionPack:
    path = PACKS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No jurisdiction pack named {name!r} in {PACKS_DIR}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return JurisdictionPack(**data)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def active_pack() -> JurisdictionPack:
    return load_pack(settings().pack_name)
