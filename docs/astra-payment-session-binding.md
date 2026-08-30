# Astra payment-session principal binding

A payment session can cap spend and expire correctly while still being used by
the wrong agent, wallet, merchant, or business operation. This profile verifies
the identity boundary between an approved session and the payment attempt that
uses it.

## Public evidence boundary

The AgentCore multi-agent payment tutorial creates two sessions for one
`USER_ID`, labels them as independent per-agent budgets, and pairs them locally:

```text
Session A -> research agent -> Coinbase instrument
Session B -> discovery agent -> Privy instrument
```

The public AgentCore Python SDK creates a payment session from `userId`, manager
ARN, expiry, limits, and an idempotency token. It does not send an agent,
payment instrument, merchant, route, or business-operation binding at session
creation. `process_payment(...)` later receives `paymentSessionId` and
`paymentInstrumentId` as independent inputs. The optional SDK `agent_name` is
propagated as an HTTP header; the public client code does not establish that
header as authorization evidence.

Public sources:

- `awslabs/agentcore-samples`, multi-agent payment orchestrator tutorial;
- `aws/bedrock-agentcore-sdk-python`,
  `src/bedrock_agentcore/payments/manager.py`.

This proves an **observability and client-contract gap**, not an AgentCore
service defect. A crossed Session A / Instrument B request may be rejected by
the backend. That stronger boundary needs a controlled service probe.

## State edge

```text
POLICY DECISION / PAYMENT SESSION
  -> PAYMENT ATTEMPT
  -> CLAIMED RESULT
  -> ACTUAL SETTLEMENT / SESSION DEBIT
  -> RECEIPT
  -> RESOURCE DELIVERY
  -> RECONCILIATION
```

## Opt-in contract

An adapter declares the dimensions that one session is expected to preserve:

```json
{
  "stage": "POLICY DECISION",
  "key": "requires_payment_session_principal_binding",
  "value": {
    "required": true,
    "dimensions": [
      "user_id",
      "agent_id",
      "payment_instrument_id",
      "merchant_origin",
      "operation_id"
    ]
  },
  "session_id": "session-a"
}
```

Supported dimensions are deliberately separate:

- `user_id` — payer/account principal;
- `agent_id` — agent or runtime principal;
- `payment_instrument_id` — wallet, account, or stored instrument;
- `merchant_origin` — normalized HTTP(S) merchant principal;
- `operation_id` — logical business obligation.

The authoritative expectation is carried by:

```json
{
  "stage": "POLICY DECISION",
  "key": "payment_session_binding",
  "authoritative": true,
  "session_id": "session-a",
  "operation_id": "operation-1",
  "value": {
    "user_id": "user-a",
    "agent_id": "research-agent",
    "payment_instrument_id": "coinbase-instrument-a",
    "merchant_origin": "https://merchant.example",
    "operation_id": "operation-1"
  }
}
```

The observed attempt carries `payment_session_use` with the same dimensions.
A local variable name, plugin object, prompt, or orchestration label is not an
authoritative session binding by itself.

A declaration may list only the dimensions the integration actually promises.
A dimension omitted from the contract is not inferred or checked.

## Merchant origin comparison

`merchant_origin` reuses Astra's strict HTTP(S) origin normalizer:

- scheme and hostname are case-normalized;
- IDNA and IPv6 are normalized;
- paths, query strings, and fragments are ignored;
- default ports are removed;
- non-default ports remain significant;
- userinfo, malformed ports, and non-HTTP(S) values are rejected.

## Findings

| Code | Meaning |
|---|---|
| `PAYMENT_SESSION_CONTRACT_INVALID` | The opt-in contract has no valid supported dimensions. |
| `PAYMENT_SESSION_BINDING_MISSING` | The session has no authoritative principal binding. |
| `PAYMENT_SESSION_BINDING_INCOMPLETE` | Required binding dimensions are absent or invalid. |
| `PAYMENT_SESSION_BINDING_CONFLICT` | Authoritative records disagree about one session's principals. |
| `PAYMENT_SESSION_USE_EVIDENCE_MISSING` | A binding exists but no attempt-context use is observed. |
| `SESSION_USE_BINDING_UNRESOLVED` | A use cannot be tied to a session or lacks required principal evidence. |
| `SESSION_USER_CROSSOVER` | The payment attempt uses a different user/account principal. |
| `SESSION_AGENT_CROSSOVER` | The payment attempt uses a different agent/runtime principal. |
| `SESSION_INSTRUMENT_CROSSOVER` | The payment attempt uses a different wallet or instrument. |
| `SESSION_MERCHANT_CROSSOVER` | The payment attempt targets a different merchant origin. |
| `SESSION_OPERATION_CROSSOVER` | The payment attempt belongs to a different business obligation. |
| `SESSION_ID_REUSED_ACROSS_OPERATIONS` | An operation-scoped session is observed across several operations. |

Missing or incomplete evidence produces `UNRESOLVED`. A principal mismatch at
the observed payment attempt produces `DIVERGED`. Neither alone proves the
backend accepted the crossed combination or that value moved.

## Fixture set

### Public-evidence fixture — unresolved

`agentcore_local_pairing_without_binding.json`

The application pairs Session A with the research agent and Coinbase instrument,
but supplies no authoritative financial binding. Expected result:

```text
PAYMENT_SESSION_BINDING_MISSING
UNRESOLVED
```

### Crossed pairing — divergent

`agentcore_session_pair_swapped.json`

Session A is bound to the research agent and Coinbase instrument, while the
observed attempt uses the discovery agent and Privy instrument. Expected:

```text
SESSION_AGENT_CROSSOVER
SESSION_INSTRUMENT_CROSSOVER
DIVERGED
```

The fixture contains no settlement event and makes no claim about AgentCore
backend acceptance.

### Cross-operation reuse — divergent

`agentcore_session_reused_cross_operation.json`

One operation-scoped session is used first for its intended operation and then
for another. Expected:

```text
SESSION_OPERATION_CROSSOVER
SESSION_ID_REUSED_ACROSS_OPERATIONS
DIVERGED
```

### Bound lifecycle — verified

`agentcore_session_bound_reconciled.json`

The binding and use agree on user, agent, instrument, merchant, and operation;
then one settlement, receipt, delivery, and terminal reconciliation complete.
Expected result: `VERIFIED`.

## Controlled service probe

Use testnet and one user with two active instruments:

1. create Session A and Session B with distinct limits;
2. run the intended A+A and B+B session/instrument controls;
3. submit Session A + Instrument B and Session B + Instrument A;
4. record whether `ProcessPayment` rejects or accepts each combination;
5. compare both session balance deltas;
6. preserve `processPaymentId`, instrument ID, merchant resource, transaction
   reference, and delivered outcome under one sanitized `operation_id`.

A rejection is useful evidence of backend-enforced isolation. Acceptance would
justify a stronger service-level crossover finding. The first probe should use
only testnet assets.

## Adjacent future contract

AgentCore's x402 `upto` tutorial documents another distinction: the session may
be debited by the buyer-signed ceiling rather than the seller's actual settled
amount. That is a separate accounting edge:

```text
authorized ceiling != actual settlement != session debit
```

It should become a dedicated Astra session-accounting profile rather than being
collapsed into principal crossover.

## Commercial boundary

This module is not another pre-execution policy engine. It verifies that the
budget context selected at runtime still belongs to the intended financial
principal and operation, and that later settlement, delivery, and reconciliation
can be attributed to that same context.
