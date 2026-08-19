"""Google ADK entrypoint.

The LLM can choose the tool, but AgentProof owns authorization, dispatch, and outcome truth.
This file follows the current Google Agents CLI / ADK project pattern.
"""
from __future__ import annotations

from .demo import demo_runtime


def run_agentproof_payment(scenario: str = "happy") -> dict:
    """Run a simulated autonomous vendor-payment workflow through AgentProof.

    Args:
        scenario: One of happy, stale, false-success, replay.

    Returns:
        A machine-readable execution receipt and ledger summary.
    """
    if scenario not in {"happy", "stale", "false-success", "replay"}:
        return {"error": "scenario must be happy, stale, false-success, or replay"}
    return demo_runtime.run(scenario)


try:  # Keep core verification tests runnable without cloud SDKs installed.
    from google.adk.agents import Agent
    from google.adk.apps import App
    from google.adk.models import Gemini
    from google.genai import types
except ImportError:  # pragma: no cover - exercised in local lightweight test env
    root_agent = None
    app = None
else:
    root_agent = Agent(
        name="agentproof_payment_agent",
        model=Gemini(
            model="gemini-3.6-flash",
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=(
            "You are an autonomous accounts-payable agent. When asked to demonstrate "
            "a payment, call run_agentproof_payment. Never claim a payment succeeded "
            "unless the returned receipt status is VERIFIED. If status is BLOCKED, "
            "UNVERIFIED, or DUPLICATE, explain the safety reason and do not override it."
        ),
        tools=[run_agentproof_payment],
    )
    app = App(root_agent=root_agent, name="app")
