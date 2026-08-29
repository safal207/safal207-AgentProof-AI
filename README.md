# AgentProof

> **Don't trust what an AI agent says it did. Prove what actually happened.**

## Problem

Autonomous AI agents are moving from conversation into execution — but an agent that *plans* an action correctly can still *execute* it wrongly. Four failure classes are invisible to ordinary agent frameworks and to the LLM itself:

1. **Stale authorization** — policy changes after planning but before execution.
2. **Action drift** — the action is mutated after authorization.
3. **False success** — an external API accepts a request, but the expected real-world state transition never occurs.
4. **Replay / duplicate execution** — the same action is dispatched again, including concurrent retries.

## Solution

AgentProof lets an agent act autonomously while independently proving:

- the action was authorized,
- the authorization was still fresh at the execution seam,
- the executed action matched the authorized action,
- the real-world state transition actually occurred (API acceptance ≠ business outcome),
- retries could not execute the same operation twice.

The hackathon demo uses a **simulated $50,000 vendor payment** to prove these failure classes. No real funds are moved by this repository.

## The core idea

```text
intent -> plan -> authorize -> REVALIDATE AT EXECUTION -> dispatch -> VERIFY OUTCOME -> receipt
```

Gemini may decide *what tool to call*, but it does **not** own the final truth of whether the action succeeded. AgentProof only emits `VERIFIED` after deterministic checks observe the expected state transition.

## Astra Spider: agent-payment verification

Astra extends AgentProof from one execution seam to the complete agentic-finance chain:

```text
REQUEST
  -> QUOTE/CHALLENGE
  -> MANDATE/AUTHORIZATION
  -> POLICY DECISION
  -> PAYMENT ATTEMPT
  -> CLAIMED RESULT
  -> ACTUAL SETTLEMENT/FINALITY
  -> RECEIPT
  -> RESOURCE/OUTCOME DELIVERY
  -> RECONCILIATION
```

It focuses on the post-decision white space that wallet and policy products do not close: whether one authorized operation produced exactly the expected economic movements, delivery, receipt, and reconciliation result.

The current suite includes five deterministic red/green fixtures covering:

- untrusted x402 response state becoming local ledger truth;
- authorization outliving the advertised challenge window;
- delivery before deferred capture;
- an intermediate funding leg incorrectly presented as completed merchant payment;
- recovery of the same logical operation through merchant settlement, delivery, and terminal reconciliation.

Run them with:

```bash
python scripts/run_astra_fixtures.py
python scripts/run_astra_fixtures.py --json
```

Each normalized trace produces a canonical SHA-256 evidence digest. The fixture runner validates both exact findings and declared `DIVERGED`, `UNRESOLVED`, or `VERIFIED` verdicts.

See:

- [`docs/astra-spider.md`](docs/astra-spider.md)
- [`docs/astra-multileg.md`](docs/astra-multileg.md)
- [`docs/astra-commercial-pilot.md`](docs/astra-commercial-pilot.md)

## Winning demo

| Scenario | Expected verdict | Money moved |
|---|---|---:|
| Happy path | `VERIFIED` | $50,000 |
| Vendor frozen after authorization | `BLOCKED` | $0 |
| Provider accepted, ledger unchanged | `UNVERIFIED` | $0 |
| Replay of the same execution key | `DUPLICATE` | still once |
| Concurrent duplicate attempts | exactly one `VERIFIED`; others `DUPLICATE` | still once |

## Why the last execution check matters

The policy decision and the provider call are separate moments in time. AgentProof revalidates the authorized action immediately before dispatch and then verifies the resulting state independently.

This closes two common gaps:

- **TOCTOU**: the world changes between authorization and execution;
- **acceptance vs outcome**: a provider returns success even though the expected business state never appears.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python -m app.demo
```

Start the API/UI:

```bash
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000>.

## API

### `POST /run`

Execute the default invoice scenario.

```bash
curl -X POST http://127.0.0.1:8000/run \
  -H 'Content-Type: application/json' \
  -d '{}'
```

### `POST /gemini/run`

Run the same governed flow through the Gemini tool-call gateway.

```bash
curl -X POST http://127.0.0.1:8000/gemini/run \
  -H 'Content-Type: application/json' \
  -d '{}'
```

### `POST /demo/reset`

Reset in-memory demo state.

## Architecture

```text
User / Agent
    |
    v
Planner / Gemini tool call
    |
    v
Policy authorization
    |
    v
Final revalidation at execution seam
    |
    v
Idempotent payment dispatch
    |
    v
Independent state verification
    |
    v
Sealed execution receipt
```

Detailed accountability and evidence boundaries are documented under `docs/`.

## Security and claim boundary

This repository is a deterministic demonstration and conformance harness, not a production custody system or a claim that all external evidence is globally complete.

- No real funds are moved by the default AgentProof demo.
- Astra treats a source as authoritative only when the integration designates and justifies that boundary.
- An absent event means it is absent from the supplied evidence, not necessarily absent from the world.
- The SHA-256 trace digest is an integrity identifier for normalized evidence, not a signature or proof of source authenticity.
- Production deployment requires durable state, authenticated evidence sources, secret management, and provider-specific adapters.

## License

Apache-2.0
