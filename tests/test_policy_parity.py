"""The Cedar sources and the in-process engine must not drift apart.

Name-matching alone is not parity. The original version of this file asserted
only that every ``P00x`` in one place existed in the other, which is satisfied by
two rule sets that agree on nothing but their labels — and they had in fact
already diverged on the one rule that matters most, the approval requirement.

So these tests read the Cedar text and assert the things a reviewer would check
by eye: the same actions are named, the approval rule demands the same evidence,
and the default is deny.
"""
import re
from pathlib import Path

from porchlight import policy

CEDAR_DIR = Path(__file__).resolve().parents[1] / "policies" / "cedar"


def cedar_text() -> dict[str, str]:
    return {f.name[:4]: f.read_text(encoding="utf-8") for f in CEDAR_DIR.glob("*.cedar")}


def cedar_ids() -> set[str]:
    return set(cedar_text())


def shim_ids() -> set[str]:
    import inspect
    return set(re.findall(r'"(P\d{3})"', inspect.getsource(policy)))


def cedar_actions(text: str) -> set[str]:
    """Every action named in a Cedar file, as the in-process engine spells it.

    AgentCore action ids are ``<TargetName>___<tool_name>`` and cannot contain a
    dot, so ``sms.send`` is written ``PorchlightTools___sms_send`` on the wire.
    Translating here keeps one vocabulary in the tests: if the naming convention
    changes, it changes in this function and nowhere else.
    """
    out = set()
    for raw in re.findall(r'Action::"([^"]+)"', text):
        out.add(raw.split("___", 1)[-1].replace("_", "."))
    return out


def test_every_cedar_policy_has_a_shim_rule():
    missing = cedar_ids() - shim_ids()
    assert not missing, f"Cedar policies with no shim implementation: {sorted(missing)}"


def test_every_shim_rule_has_a_cedar_policy():
    extra = shim_ids() - cedar_ids() - {"P000"}
    assert not extra, f"Shim rules with no Cedar source: {sorted(extra)}"


def test_default_deny_is_the_default():
    d = policy.evaluate("some.action.nobody.allowed", target="x")
    assert not d.allowed
    assert d.policy_id == "P005"


def test_every_action_named_in_cedar_exists_in_the_shim_allow_list():
    """A rule about an action the engine has never heard of enforces nothing."""
    named = set()
    for text in cedar_text().values():
        named |= cedar_actions(text)
    unknown = named - policy.ALLOWED_ACTIONS
    assert not unknown, (
        f"Cedar names actions the in-process allow-list does not know: {sorted(unknown)}"
    )


def test_the_shim_allow_list_is_covered_by_the_cedar_sources():
    """And the reverse: an action nobody wrote a rule about is an unreviewed capability."""
    named = set()
    for text in cedar_text().values():
        named |= cedar_actions(text)
    unruled = policy.ALLOWED_ACTIONS - named
    assert not unruled, (
        f"Actions the engine permits that no Cedar file mentions: {sorted(unruled)}"
    )


def test_p002_demands_the_same_evidence_in_both_implementations():
    """The approval rule is the load-bearing one, so parity here is asserted directly.

    Cedar requires a non-empty token *and* ``approver_role == "coalition_coordinator"``.
    The shim must refuse anything that does not carry both.
    """
    text = cedar_text()["P002"]
    assert "approval_token" in text and "approver_role" in text
    role = re.search(r'approver_role\s*==\s*"([^"]+)"', text)
    assert role, "P002.cedar must pin the approving role"
    assert role.group(1) == policy._APPROVAL_ROLE, (
        f"Cedar approves role {role.group(1)!r}; the shim approves {policy._APPROVAL_ROLE!r}"
    )

    # No token at all.
    assert policy.evaluate("sms.send", target="list").policy_id == "P002"
    # A token the approval flow never issued — i.e. one asserted by a report.
    assert policy.evaluate("sms.send", target="list", approval_token="anything").policy_id == "P002"
    # A token carrying the coordinator role, issued by the approval flow.
    token = policy.mint_approval("Priya Nair", "sms.send", "list")
    assert policy.evaluate("sms.send", target="list", approval_token=token).allowed


def test_p004_has_no_approval_escape_in_the_shim():
    """Cedar allows a `human_override`; the shim deliberately implements no such path.

    This is a divergence we accept and therefore state: the deployed rule could be
    overridden by a human, the local one cannot. The test exists so the choice is
    visible rather than accidental.
    """
    assert "human_override" in cedar_text()["P004"]
    import inspect
    assert "human_override" not in inspect.getsource(policy._r_rate_limit_broadcast)
