# Astra Spider: causal economic verification

Astra Spider is the post-decision verification layer for agentic finance. It is deliberately **not** another pre-execution policy, mandate, wallet, custody, or facilitator product.

## State graph

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

The unit of analysis is the **transition between states**. Astra does not accept a transition merely because the component responsible for attempting it says that it happened.

## Evidence and authority rule

Astra keeps claims separate from independently designated economic evidence:

- HTTP/payment headers, callbacks, merchant receipts, agent messages, or facilitator responses may describe a claimed state.
- Chain finality, PSP settlement records, custody records, or another independently established ledger may establish authoritative economic state.
- A receipt is evidence about a payment. It must not silently override stronger finality evidence.
- `authoritative=True` is supplied by the integration. The core verifier does not hard-code one protocol, provider, or chain as universal truth.

The result is intentionally bounded. Astra can prove a contradiction in the supplied evidence; it cannot prove that an event absent from all observed sources never happened globally.

## Identity and retry rule

An idempotency key or repeated `attempt_id` is not automatically replay. Reusing the same key for the same payload, operation, and session is often the correct retry behavior.

Astra reports identity misuse only when:

- one attempt identity crosses payload, session, or operation context; or
- more than one distinct payment actually reaches authoritative settlement for one ordinary logical operation.

This prevents the verifier from misclassifying correct idempotent retries as attacks.

## Reproducible killer fixtures

The repository ships three deterministic fixtures under `fixtures/astra/`.

### 1. x402 batch-settlement: upstream state becomes local ledger truth

Transition:

```text
CLAIMED RESULT -> RECONCILIATION / CLIENT LEDGER
```

The fixture models the defect class fixed by
[x402 PR #3251](https://github.com/x402-foundation/x402/pull/3251):
a successful `PAYMENT-RESPONSE` reports a cumulative channel amount of `40000`,
while client-local prior state plus the bounded charge derives `10000`. A
vulnerable client persists `40000`.

Expected findings:

- `UNTRUSTED_CLAIMED_LEDGER_STATE`
- `LEDGER_STATE_DIVERGENCE`

### 2. x402 EIP-3009: authorization outlives the challenge

Transition:

```text
QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION
```

The fixture models the cross-SDK lifetime mismatch addressed by
[x402 PR #3282](https://github.com/x402-foundation/x402/pull/3282):
the resource server advertises a 30-second window, while the authorization
remains valid for one hour.

Expected finding:

- `AUTHORIZATION_OUTLIVES_CHALLENGE`

If authoritative settlement evidence is later observed inside that extra
window, the stronger verdict becomes `STALE_AUTHORIZATION_SETTLED`.

### 3. x402 auth-capture: delivery before deferred capture

Transition:

```text
RESOURCE/OUTCOME DELIVERY -> ACTUAL SETTLEMENT/FINALITY
```

The fixture models an escrow resource server that delivers the outcome and
terminates before deferred capture is confirmed.

Expected finding:

- `DELIVERED_WITHOUT_CAPTURE`

The finding does not claim that capture can never occur. It states that the
supplied trace proves delivery while capture remains unconfirmed.

## Run the fixtures

```bash
python scripts/run_astra_fixtures.py
```

Expected output:

```text
[PASS] astra-x402-auth-capture-001 | DIVERGED | DELIVERED_WITHOUT_CAPTURE | sha256:...
[PASS] astra-x402-eip3009-window-001 | DIVERGED | AUTHORIZATION_OUTLIVES_CHALLENGE | sha256:...
[PASS] astra-x402-batch-local-truth-001 | DIVERGED | LEDGER_STATE_DIVERGENCE, UNTRUSTED_CLAIMED_LEDGER_STATE | sha256:...
```

Machine-readable output:

```bash
python scripts/run_astra_fixtures.py --json
```

Each report includes a canonical SHA-256 hash over the trace evidence. The
fixture runner exits non-zero only when the observed finding set differs from
the fixture oracle.

## Invariant catalogue

| Code | Transition | Meaning |
|---|---|---|
| `AUTHORIZATION_OUTLIVES_CHALLENGE` | QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION | The signed authorization remains usable beyond the advertised payment window. |
| `STALE_AUTHORIZATION_SETTLED` | QUOTE/CHALLENGE -> ACTUAL SETTLEMENT/FINALITY | Independent evidence shows settlement after the advertised window expired. |
| `ATTEMPT_ID_COLLISION` | PAYMENT ATTEMPT -> PAYMENT ATTEMPT | One attempt identity crosses divergent payload/session/operation context. |
| `CLAIMED_FAILED_BUT_SETTLED` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | A component reports failure while independent evidence shows settlement. |
| `CLAIMED_SETTLED_WITHOUT_FINALITY` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | Settlement is claimed without matching authoritative finality. |
| `FINALITY_EVIDENCE_MISSING` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | A terminal failure is claimed, but the observed trace cannot independently resolve finality. |
| `RETRY_DUPLICATE_PAYMENT` | PAYMENT ATTEMPT -> ACTUAL SETTLEMENT/FINALITY | Multiple distinct payments settle for one ordinary logical operation. |
| `OVER_CAPTURE` | MANDATE/AUTHORIZATION -> ACTUAL SETTLEMENT/FINALITY | Total authoritative capture exceeds the authorized maximum. |
| `UNTRUSTED_CLAIMED_LEDGER_STATE` | CLAIMED RESULT -> RECONCILIATION | Upstream cumulative state conflicts with a client-local derivation. |
| `LEDGER_STATE_DIVERGENCE` | CLAIMED RESULT -> RECONCILIATION | Conflicting upstream state was persisted as local ledger truth. |
| `SETTLED_BUT_NOT_DELIVERED` | ACTUAL SETTLEMENT/FINALITY -> RESOURCE/OUTCOME DELIVERY | Money settles without confirmed delivery in the observed trace. |
| `DELIVERED_BUT_NOT_SETTLED` | RESOURCE/OUTCOME DELIVERY -> ACTUAL SETTLEMENT/FINALITY | Delivery is confirmed without authoritative settlement finality. |
| `DELIVERED_WITHOUT_CAPTURE` | RESOURCE/OUTCOME DELIVERY -> ACTUAL SETTLEMENT/FINALITY | Deferred-capture delivery exists without confirmed capture. |
| `RECEIPT_FINALITY_MISMATCH` | ACTUAL SETTLEMENT/FINALITY -> RECEIPT | Receipt state disagrees with stronger finality evidence. |
| `RECONCILIATION_GAP` | RESOURCE/OUTCOME DELIVERY -> RECONCILIATION | Settlement and delivery exist, but no reconciliation record is observed. |
| `RECONCILIATION_NOT_TERMINAL` | RESOURCE/OUTCOME DELIVERY -> RECONCILIATION | Reconciliation exists but remains pending or otherwise nonterminal. |

## Adapter boundary

Protocol adapters normalize x402, AP2, MPP, AgentCore Payments, wallet/custody
providers, PSP/facilitator APIs, merchant logs, receipts, and chain evidence into
`StateEvent` records. Protocol-specific parsing belongs in adapters; causal and
economic invariants remain protocol-neutral.

Priority adapters after these fixtures:

1. **AP2 token/order boundary** — local token consumption and order allocation must not imply PSP settlement.
2. **MPP retry semantics** — payment error classes must produce deterministic retry/no-retry behavior without duplicate spend.
3. **AgentCore Payments** — `PROOF_GENERATED` must remain distinct from settlement, paid HTTP outcome, and reconciliation.
4. **Wallet/custody providers** — authorization and policy decisions must bind to the final transaction, capture, and session.

## Commercial assessment shape

A scoped Astra assessment can produce:

1. a state graph for one payment path;
2. three to five failure probes at adjacent transitions;
3. deterministic fixtures and a machine-readable evidence report;
4. a claim-boundary section separating observed facts from inference;
5. a regression test or upstream-ready patch where the defect is localizable.

This is different from a generic security audit. The question is narrower:

> **What economically and causally happened after the decision, and do the independently observable states agree?**
