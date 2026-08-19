from __future__ import annotations

import os
from typing import Any

from .demo import demo_runtime


class GeminiUnavailable(RuntimeError):
    pass


SYSTEM_INSTRUCTION = """
You are AgentProof's autonomous accounts-payable agent for a hackathon demo.
You must take action, not merely discuss it. For every user goal, call the
run_agentproof_payment tool exactly once and choose the scenario that best
matches the user's request:
- happy: normal approved payment
- stale: policy/vendor state changes after authorization but before dispatch
- false-success: provider accepts the request but settlement is absent
- replay: the same execution is attempted twice

The tool's machine-readable receipt is authoritative. Never claim success
unless receipt.status is VERIFIED. BLOCKED, UNVERIFIED, and DUPLICATE are
safety outcomes and must never be rewritten as success.
""".strip()


def run_goal(goal: str) -> dict[str, Any]:
    """Let Gemini choose and execute one AgentProof demonstration tool call."""
    if not goal.strip():
        raise ValueError("goal cannot be blank")

    using_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true", "yes"}
    if not os.getenv("GEMINI_API_KEY") and not using_vertex:
        raise GeminiUnavailable(
            "Gemini credentials are not configured. Set GEMINI_API_KEY or configure Vertex AI ADC."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - cloud dependency path
        raise GeminiUnavailable("google-genai is not installed") from exc

    executions: list[dict[str, Any]] = []

    def run_agentproof_payment(scenario: str = "happy") -> dict[str, Any]:
        """Execute a verified vendor-payment workflow.

        Args:
            scenario: One of happy, stale, false-success, replay.
        """
        if scenario not in {"happy", "stale", "false-success", "replay"}:
            result = {"error": "scenario must be happy, stale, false-success, or replay"}
        else:
            result = demo_runtime.run(scenario)
        executions.append(result)
        return result

    model = os.getenv("AGENTPROOF_GEMINI_MODEL", "gemini-3.6-flash")
    client = genai.Client()

    try:
        response = client.models.generate_content(
            model=model,
            contents=goal,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[run_agentproof_payment],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=2
                ),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="ANY")
                ),
            ),
        )
    except Exception as exc:  # pragma: no cover - depends on external Gemini service
        raise GeminiUnavailable(f"Gemini request failed: {exc}") from exc

    if not executions:
        raise GeminiUnavailable("Gemini returned without executing the required AgentProof tool.")

    execution = executions[-1]
    return {
        "model": model,
        "goal": goal,
        "agent_message": response.text or "AgentProof tool executed.",
        "execution": execution,
    }
