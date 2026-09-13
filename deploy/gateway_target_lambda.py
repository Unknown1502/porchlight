"""Stub Lambda backing the PorchlightGateway's MCP target.

What this is for: AgentCore Gateway only recognizes an action id like
``AgentCore::Action::"PorchlightTools___sms_send"`` once a target named
PorchlightTools actually exists and declares a tool called ``sms_send``. Cedar
validation against the gateway's schema rejects an action nobody registered —
that is the wall documented in policies/README.md. This Lambda is what lets the
gateway's schema know these 15 tool names exist, so the five Cedar policies can
be loaded and reach ACTIVE against a real gateway ARN.

What this is NOT: Porchlight's running server does not call this Lambda. Every
tool call in the demo, the tests and the container still goes straight to the
Python functions in src/porchlight — this stub is not in that path. Wiring the
app to actually invoke tools through the gateway (so this Lambda's response is
what the agent sees) is separate, larger work, tracked as not done.
"""
import json


def handler(event, context):
    tool_name = (
        event.get("tool", {}).get("name")
        or event.get("toolName")
        or event.get("name")
        or "unknown"
    )
    return {
        "status": "stub",
        "tool": tool_name,
        "note": (
            "PorchlightGateway target stub. Registered only so AgentCore Policy "
            "can validate and enforce Cedar rules against real per-tool action "
            "ids. Not wired to live execution."
        ),
        "received": event,
    }
