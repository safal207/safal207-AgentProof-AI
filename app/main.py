from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .demo import demo_runtime
from .gemini_gateway import GeminiUnavailable, run_goal

web_app = FastAPI(
    title="AgentProof",
    description="Verifiable execution for autonomous AI agents.",
    version="0.1.0",
)

STATIC = Path(__file__).parent / "static"
MEDIA = STATIC / "media"

web_app.mount("/media", StaticFiles(directory=MEDIA), name="media")


@web_app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@web_app.get("/health")
def health():
    return {"status": "ok", "service": "agentproof", "version": "0.1.0"}


@web_app.post("/api/demo/{scenario}")
def run_demo(scenario: str):
    try:
        return demo_runtime.run(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AgentGoal(BaseModel):
    goal: str


@web_app.post("/api/agent")
def run_agent(request: AgentGoal):
    try:
        return run_goal(request.goal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeminiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@web_app.get("/api/state")
def state():
    return {
        "policy_epoch": demo_runtime.state.policy_epoch,
        "frozen_vendors": sorted(demo_runtime.state.frozen_vendors),
        "ledger": demo_runtime.state.ledger,
        "execution_index": demo_runtime.state.execution_index,
    }


# Cloud Run / uvicorn convention: expose variable named app.
app = web_app
