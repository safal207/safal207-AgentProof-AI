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
- `authoritative=True` is supplied by the integration. The core verifier does not hard-code one provider, protocol, or chain as universal truth.

The result is intentionally bounded. Astra can prove a contradiction in the supplied evidence; it cannot prove that an event absent from all observed sources never happened globally.

## Numeric correctness

Monetary values are parsed with exact `Decimal` arithmetic rather than binary floating point. This matters for atomic values above `2^53`, decimal-string amounts, and partial captures.

When the same economic amount is exposed through several views, Astra uses one precedence class rather than adding synonymous fields together:

```text
captured_amount_minor
  -> charged_amount_minor
  -> settled_amount_minor
```

This prevents a receipt that contains both `settled_amount` and `captured_amount` from being misread as two charges.

## Identity, retry, and multi-settlement rule

An idempotency key or repeated `attempt_id` is not automatically replay. Reusing the same key for the same payload, operation, and session is often the correct retry behavior.

Astra reports identity misuse only when:

- one attempt identity crosses payload, session, or operation context; or
- more distinct payments settle than the declared lifecycle permits.

For ordinary one-payment flows, two distinct settled payment IDs produce `RETRY_DUPLICATE_PAYMENT`.

For batch, partial, installment, auth-capture, or escrow flows, the adapter should supply `expected_settlement_count`. Without that bound, multiple settlements produce `MULTI_SETTLEMENT_UNRESOLVED` rather than a false duplicate-payment accusation.

For economically distinct multi-leg flows, the adapter declares `required_settlement_legs`. Each leg needs its own authoritative completion evidence; an intermediate funding or reserve leg must not silently become terminal success for the merchant obligation.

## Deterministic evidence hash

Each trace report includes:

```text
hash_profile = astra-trace-json-v1
evidence_hash = SHA-256(deterministic supported-profile JSON)
```

The profile sorts object keys, fixes separators, preserves UTF-8, rejects non-finite floats, serializes exact decimals as strings, and rejects naive datetimes. The hash is an integrity identifier for the normalized trace. It is **not** a digital signature and is not claimed to be RFC 8785/JCS.

Fixture-oracle fields (`expected_codes` and `expected_verdict`) are intentionally excluded from the evidence hash so changing an expected result does not change the preserved input evidence.

## Reproducible killer fixtures

The repository ships five deterministic fixtures under `fixtures/astra/`.

### 1. x402 batch-settlement: upstream state becomes local ledger truth

Transition:

```text
CLAIMED RESULT -> RECONCILIATION / CLIENT LEDGER
```

The fixture models the defect class fixed by [x402 PR #3251](https://github.com/x402-foundation/x402/pull/3251): a successful `PAYMENT-RESPONSE` reports a cumulative channel amount of `40000`, while client-local prior state plus the bounded charge derives `10000`. A vulnerable client persists `40000`.

Expected findings:

- `UNTRUSTED_CLAIMED_LEDGER_STATE`
- `LEDGER_STATE_DIVERGENCE`

### 2. x402 EIP-3009: authorization outlives the challenge

Transition:

```text
QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION
```

The fixture models the cross-SDK lifetime mismatch addressed by [x402 PR #3282](https://github.com/x402-foundation/x402/pull/3282): the resource server advertises a 30-second window, while the authorization remains valid for one hour.

Expected finding:

- `AUTHORIZATION_OUTLIVES_CHALLENGE`

If authoritative settlement evidence is later observed inside that extra window, the stronger verdict becomes `STALE_AUTHORIZATION_SETTLED`.

### 3. x402 auth-capture: delivery before deferred capture

Transition:

```text
RESOURCE/OUTCOME DELIVERY -> ACTUAL SETTLEMENT/FINALITY
```

The fixture models an escrow resource server that delivers the outcome and terminates before deferred capture is confirmed.

Expected finding:

- `DELIVERED_WITHOUT_CAPTURE`

The finding does not claim that capture can never occur. It states that the supplied trace proves delivery while capture remains unconfirmed.

### 4. x402 two-leg crash: funding is not merchant settlement

Transition:

```text
FUNDING FINALITY -> CLAIMED RESULT -> MERCHANT SETTLEMENT / DELIVERY / RECONCILIATION
```

The divergent fixture models the boundary documented by Haven-AI issue `#2145`: the funding leg is independently confirmed, the merchant leg has no completion evidence, yet the status surface claims `payment_confirmed` and `next_action: none`.

Expected verdict: `DIVERGED`.

Expected findings:

- `FUNDED_BUT_MERCHANT_UNSETTLED`
- `PARTIAL_SETTLEMENT_CLAIMED_COMPLETE`
- `RECOVERY_ACTION_MISSING`

### 5. x402 two-leg recovery: resume the original operation

The paired green fixture keeps one logical `operation_id` through funding, a server-derived retry action, resumption of the original operation, one merchant settlement, receipt, delivery, and terminal reconciliation.

Expected verdict: `VERIFIED`.

Expected findings: none.

This normalized shape is supported by Haven-AI's public Base-Sepolia QA evidence: after the recovery configuration was corrected, all 14 scenarios passed, the original payment resumed, 0.001 USDC moved treasury to merchant, and the delegate returned to baseline. The fixture uses sanitized identifiers and is not a verbatim export of Haven runtime data.

## Run the fixtures

```bash
python scripts/run_astra_fixtures.py
```

Machine-readable output:

```bash
python scripts/run_astra_fixtures.py --json
```

The runner exits non-zero when the observed finding set or a declared `expected_verdict` differs from the fixture oracle. CI executes the fixture runner on Python 3.11 and 3.12 in addition to the unit-test suite.

## Invariant catalogue

| Code | Transition | Meaning |
|---|---|---|
| `AUTHORIZATION_OUTLIVES_CHALLENGE` | QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION | The signed authorization remains usable beyond the advertised payment window. |
| `STALE_AUTHORIZATION_SETTLED` | QUOTE/CHALLENGE -> ACTUAL SETTLEMENT/FINALITY | Independent evidence shows settlement after the advertised window expired. |
| `ATTEMPT_ID_COLLISION` | PAYMENT ATTEMPT -> PAYMENT ATTEMPT | One attempt identity crosses divergent payload/session/operation context. |
| `CLAIMED_FAILED_BUT_SETTLED` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | A component reports failure while independent evidence shows settlement. |
| `CLAIMED_SETTLED_WITHOUT_FINALITY` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | Settlement is claimed without matching authoritative finality. |
| `FINALITY_EVIDENCE_MISSING` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | A terminal result is claimed, but the observed trace cannot independently resolve finality. |
| `RETRY_DUPLICATE_PAYMENT` | PAYMENT ATTEMPT -> ACTUAL SETTLEMENT/FINALITY | More payments settle than the logical operation permits. |
| `MULTI_SETTLEMENT_UNRESOLVED` | PAYMENT ATTEMPT -> ACTUAL SETTLEMENT/FINALITY | Multiple settlements exist in a multi-settlement mode, but the expected lifecycle count is absent. |
| `FUNDED_BUT_MERCHANT_UNSETTLED` | ACTUAL SETTLEMENT/FINALITY -> RESOURCE/OUTCOME DELIVERY | An intermediate funding leg is complete while merchant settlement is not established. |
| `PARTIAL_SETTLEMENT_OUTCOME_UNRESOLVED` | ACTUAL SETTLEMENT/FINALITY -> RECONCILIATION | Some declared economic legs are complete and others remain unresolved. |
| `PARTIAL_SETTLEMENT_CLAIMED_COMPLETE` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | A status surface claims completion while a required economic leg remains unresolved. |
| `RECOVERY_ACTION_MISSING` | CLAIMED RESULT -> RECONCILIATION | The status surface says no action is required while a required leg remains unresolved. |
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

Protocol adapters normalize x402, AP2, MPP, AgentCore Payments, wallet/custody providers, PSP/facilitator APIs, merchant logs, receipts, and chain evidence into `StateEvent` records. Protocol-specific parsing belongs in adapters; causal and economic invariants remain protocol-neutral.

Priority adapters after these fixtures:

1. **Portable multi-leg adapter** — map the same crash/resume contract onto a second bridge, delegated-funding, escrow, or auth-capture system.
2. **AP2 token/order boundary** — local token consumption and order allocation must not imply PSP settlement.
3. **MPP retry semantics** — payment error classes must produce deterministic retry/no-retry behavior without duplicate spend.
4. **AgentCore Payments** — `PROOF_GENERATED` must remain distinct from settlement, paid HTTP outcome, and reconciliation.
5. **Wallet/custody providers** — authorization and policy decisions must bind to the final transaction, capture, and session.

## Commercial assessment shape

A scoped Astra assessment can produce:

1. a state graph for one payment path;
2. three to five failure probes at adjacent transitions;
3. deterministic red/green fixtures and a machine-readable evidence report;
4. a claim-boundary section separating observed facts from inference;
5. a regression test or upstream-ready patch where the defect is localizable.

This is different from a generic security audit. The question is narrower:

> **What economically and causally happened after the decision, and do the independently observable states agree?**
