# Astra Spider: causal economic verification

Astra Spider is the post-decision verification layer for agentic finance. It is deliberately **not** another pre-execution policy, mandate, authorization, wallet, custody, or facilitator product.

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

The core unit of analysis is the **edge between adjacent states**. A transition is not accepted merely because the upstream component says it occurred.

## Evidence rule

Astra keeps *claim evidence* separate from *authoritative economic evidence*.

Examples:

- HTTP/payment headers, merchant receipts, callbacks, agent messages, or facilitator responses can describe a claimed state.
- Chain finality, PSP settlement evidence, or another explicitly designated independent ledger can establish authoritative settlement state.
- A receipt is evidence about a payment; it must not silently become the authority for the underlying economic state when a stronger source exists.

The verifier therefore supports a caller-supplied `authoritative=True` boundary rather than hard-coding one protocol or provider as truth.

## Initial invariant catalogue

| Code | Transition | Meaning |
|---|---|---|
| `CLAIMED_FAILED_BUT_SETTLED` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | Upstream reports failure while authoritative evidence shows settlement. |
| `CLAIMED_SETTLED_WITHOUT_FINALITY` | CLAIMED RESULT -> ACTUAL SETTLEMENT/FINALITY | Upstream reports settlement while authoritative evidence does not. |
| `RETRY_DUPLICATE_PAYMENT` | PAYMENT ATTEMPT -> ACTUAL SETTLEMENT/FINALITY | More than one distinct payment settles for one causal trace. |
| `REPLAYED_ATTEMPT_ID` | PAYMENT ATTEMPT -> PAYMENT ATTEMPT | One attempt identity is reused. |
| `SETTLED_BUT_NOT_DELIVERED` | ACTUAL SETTLEMENT/FINALITY -> RESOURCE/OUTCOME DELIVERY | Money settles without confirmed resource/outcome delivery. |
| `DELIVERED_BUT_NOT_SETTLED` | RESOURCE/OUTCOME DELIVERY -> ACTUAL SETTLEMENT/FINALITY | Delivery is confirmed without authoritative settlement finality. |
| `RECEIPT_FINALITY_MISMATCH` | ACTUAL SETTLEMENT/FINALITY -> RECEIPT | Receipt disagrees with stronger settlement evidence. |
| `RECONCILIATION_GAP` | RESOURCE/OUTCOME DELIVERY -> RECONCILIATION | Settlement and delivery exist but no terminal reconciliation record exists. |

## Claim boundary

Astra can prove a **mismatch within the evidence supplied to the verifier**. It cannot prove global completeness of the world, that an absent event never occurred, or that a specific external system is authoritative unless that authority is established independently by the integration.

Consequently:

- `SETTLED_BUT_NOT_DELIVERED` means no confirming delivery event is present in the supplied trace after authoritative settlement evidence.
- It does **not** mean the resource was impossible to deliver or was never delivered outside the observed sources.
- `RECONCILIATION_GAP` means no terminal reconciliation record is present in the observed trace.
- It does **not** prove that no reconciliation exists in an unobserved system.

This distinction is central to Astra: detect causal/economic inconsistencies without overstating global completeness.

## Protocol adapters

Adapters should normalize x402, AP2, MPP, AgentCore Payments, wallet/custody providers, PSP/facilitator APIs, and merchant logs into `StateEvent` records. Protocol-specific parsing belongs in adapters; economic invariants stay protocol-neutral.

Priority adapters/probes:

1. **x402 claimed-state divergence** — facilitator/payment response versus independent chain/PSP finality.
2. **x402 auth-capture deferred crash** — resource delivered, worker dies before capture, then restart/reclaim.
3. **MPP failure semantics** — classify retry/no-retry behavior and ensure one failed logical request cannot produce duplicate settlement.
4. **AP2 token/order settlement boundary** — local token/order state must not imply settlement finality when the PSP call failed.

## Market boundary

Pre-execution controls are increasingly crowded: policy engines, spend mandates, wallet controls, custody rules, allow/deny authorization, and facilitator verification answer **"may this action execute?"**

Astra answers a different question:

> **"What economically and causally happened after the decision, and do the independently observable states agree?"**

That post-decision boundary covers settlement, finality, receipts, delivery, retries, partial/batch outcomes, and reconciliation.
