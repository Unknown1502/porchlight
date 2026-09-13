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

    # Real CloudWatch Logs group for policy denial records. Empty (the default)
    # means denials are recorded only in the local audit table — correct for
    # tests and the offline demo, which must not reach AWS. Set this to mirror
    # every denial to CloudWatch as well; see policy.py's _cloudwatch_log.
    cloudwatch_log_group: str = os.getenv("PORCHLIGHT_CLOUDWATCH_LOG_GROUP", "")
    urlhaus_key: str = os.getenv("URLHAUS_AUTH_KEY", "")

    # Where the transactional store lives. Empty => porchlight.db at the repo
    # root. Tests point this at a temp file so a run never touches demo state.
    db_path: str = os.getenv("PORCHLIGHT_DB", "")

    # Shared secret for POST /reports. Empty means the webhook is open, which is
    # correct for a local demo and is surfaced in the UI as such — an unlabelled
    # open ingest endpoint is worse than an obvious one.
    ingest_token: str = os.getenv("PORCHLIGHT_INGEST_TOKEN", "")

    # Per-coordinator shared secrets for the approval endpoints, "name:secret"
    # pairs separated by commas — e.g. "Priya Nair:tok_abc,Sam Osei:tok_def".
    #
    # Empty (the default) means /approve trusts the X-Coordinator header's name
    # on its own — real for a local demo, but it is *naming* an approver, not
    # *authenticating* one, and that gap stays labelled everywhere it appears
    # rather than being implied away. Setting this closes it: an approval then
    # requires the secret registered to that exact name, so report content or an
    # unauthenticated caller cannot mint a capability by asserting a name. It is
    # still not an IdP — no session, no rotation, no revocation list — and does
    # not pretend to be one; see README Limitations.
    coordinator_credentials: str = os.getenv("PORCHLIGHT_COORDINATOR_CREDENTIALS", "")

    # Fixture-backed corroboration. Default ON: the demo must not depend on a
    # third-party feed being up, and it must never touch attacker infrastructure.
    # Set to 0 to consult the real allow-listed feeds.
    use_fixture_tools: bool = os.getenv("PORCHLIGHT_FIXTURE_TOOLS", "1") == "1"

    # How long a coordinator approval stays valid. Short on purpose: an approval
    # is permission to send this message now, not a standing authorisation.
    approval_ttl_minutes: int = int(os.getenv("PORCHLIGHT_APPROVAL_TTL_MIN", "30"))

    # Bound on model/tool work per report, so a pathological input cannot run up
    # a bill or wedge the queue.
    max_tool_calls_per_report: int = int(os.getenv("PORCHLIGHT_MAX_TOOL_CALLS", "8"))

    # Campaign correlation thresholds. Deliberately conservative: a false
    # "campaign" broadcast to a whole senior list is worse than a missed one.
    campaign_min_reports: int = 3
    campaign_min_distinct_reporters: int = 3
    campaign_window_days: int = 14

    @property
    def use_agentcore_memory(self) -> bool:
        return bool(self.memory_id)

    @property
    def coordinator_secrets(self) -> dict[str, str]:
        """Parsed ``name -> secret`` map from ``coordinator_credentials``.

        Malformed entries (no ``:``, empty name, empty secret) are dropped
        rather than raising, so a typo in one entry does not take the whole
        endpoint down — it just means that one name has no working credential,
        which /approve will report as plainly as any other auth failure.
        """
        pairs: dict[str, str] = {}
        for entry in self.coordinator_credentials.split(","):
            name, sep, secret = entry.strip().partition(":")
            if sep and name.strip() and secret.strip():
                pairs[name.strip()] = secret.strip()
        return pairs


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
