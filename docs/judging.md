# Judging alignment

## Innovation & Operational Utility
AgentProof removes the human burden of manually supervising every high-value agent action while preventing stale, duplicated, or falsely reported execution.

## Architectural Discipline
- LLM proposes action; deterministic runtime owns execution truth.
- Short-lived, action-bound authorization grants.
- Revalidation at the dispatch seam.
- Atomic idempotency claim for concurrent retries.
- Outcome grounding against observable ledger state.
- Machine-readable evidence receipts with integrity digest.

## Demo & Production Readiness
- FastAPI visual demo.
- Google ADK entrypoint with Gemini 3.6 Flash.
- Direct Gemini goal endpoint for live autonomous tool calling.
- Cloud Run-ready Dockerfile.
- Reproducible pytest suite including concurrency behavior.
- Mermaid architecture and 120-second demo script.
