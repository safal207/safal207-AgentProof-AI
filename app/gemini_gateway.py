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

_SCENARIOS = frozenset({"happy", "stale", "false-success", "replay"})
_TOOL_NAME = "run_agentproof_payment"


def _required_scenario(goal: str) -> str | None:
    """Lock explicit safety-demo goals to their deterministic scenario.

    This is intentionally narrow. Gemini remains responsible for an ordinary
    goal, but an explicit request describing a known safety condition must not
    be weakened by a model choosing the happy path.
    """
    normalized = " ".join(goal.lower().split())

    if any(marker in normalized for marker in ("replay", "duplicate", "retry")):
        return "replay"
    if any(
        marker in normalized
        for marker in (
            "false-success",
            "false success",
            "accepted but not settled",
            "accepted without settlement",
            "provider accepts but",
        )
    ):
        return "false-success"
    if any(
        marker in normalized
        for marker in (
            "stale authorization",
            "vendor frozen",
            "vendor freeze",
            "frozen after authorization",
            "freeze after authorization",
            "after approval",
        )
    ):
        return "stale"
    return None


def run_goal(goal: str) -> dict[str, Any]:
    """Ask Gemini for one tool call, then execute it through AgentProof."""
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

    payment_tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=_TOOL_NAME,
                description=(
                    "Execute one AgentProof vendor-payment demonstration. "
                    "The returned receipt is the sole authority for the final verdict."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "string",
                            "enum": sorted(_SCENARIOS),
                            "description": "The safety scenario requested by the user.",
                        }
                    },
                    "required": ["scenario"],
                },
            )
        ]
    )

    model = os.getenv("AGENTPROOF_GEMINI_MODEL", "gemini-3.6-flash")
    client = genai.Client()

    try:
        response = client.models.generate_content(
            model=model,
            contents=goal,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[payment_tool],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=[_TOOL_NAME]
                    )
                ),
            ),
        )
    except Exception as exc:  # pragma: no cover - depends on external Gemini service
        raise GeminiUnavailable(f"Gemini request failed: {exc}") from exc

    tool_calls = getattr(response, "function_calls", None) or []
    if len(tool_calls) != 1:
        raise GeminiUnavailable("Gemini returned without exactly one required AgentProof tool call.")

    tool_call = tool_calls[0]
    if getattr(tool_call, "name", None) != _TOOL_NAME:
        raise GeminiUnavailable("Gemini returned an unexpected tool call.")

    arguments = dict(getattr(tool_call, "args", None) or {})
    requested_scenario = arguments.get("scenario", "happy")
    if not isinstance(requested_scenario, str) or requested_scenario not in _SCENARIOS:
        raise GeminiUnavailable("Gemini returned an invalid AgentProof scenario.")

    locked_scenario = _required_scenario(goal)
    executed_scenario = locked_scenario or requested_scenario
    execution = demo_runtime.run(executed_scenario)

    return {
        "model": model,
        "goal": goal,
        "agent_message": response.text or "Gemini requested AgentProof execution.",
        "tool_call": {
            "name": _TOOL_NAME,
            "model_requested_scenario": requested_scenario,
            "executed_scenario": executed_scenario,
            "scenario_lock_applied": locked_scenario is not None
            and locked_scenario != requested_scenario,
        },
        "verdict_authority": "AgentProof deterministic receipt",
        "execution": execution,
    }
