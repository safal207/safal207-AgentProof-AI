# Astra settlement-gated protected delivery

A payment can verify successfully while settlement still fails. A merchant may
run business logic and generate protected output before settlement, but that
output must remain private and discardable until authoritative finality says the
payment settled.

This profile verifies the ordering boundary between verification, handler
execution, response buffering, settlement, public response commit, and delivery.

## Public evidence

`x402-foundation/x402#3068` documented a concrete Java SDK defect:
`PaymentFilter.doFilter()` called the protected servlet chain against the real
`HttpServletResponse` before `facilitator.settle()`. If settlement then failed,
the response was usually already committed and the premium body had reached the
buyer.

Merged PR `x402-foundation/x402#3074`, authored by `@rileybuilds`, added a
buffering servlet wrapper. Its embedded-Jetty regression reproduced the pre-fix
result as HTTP 200 plus leaked body and the fixed result as HTTP 402 with the
protected body discarded. The fix merged on 4 September 2026 at commit
`5df361d591fd3df74eb363296347b2ed57e8f413`.

The same PR exposed a useful test-quality signal: an existing “valid payment”
test used a settlement stub whose default response was `success=false`, yet the
test passed before the fix because delivery occurred before settlement outcome
mattered.

The TypeScript/Express middleware already buffered protected output. The Java
case therefore provides a public cross-SDK ordering divergence and a concrete
red/green regression shape.

Public channels:

- issue reporter `@Sertug17`, `x402-foundation/x402#3068`;
- fix author `@rileybuilds`, `x402-foundation/x402#3074`;
- no personal email is asserted.

## State edge

```text
PAYMENT ATTEMPT
  -> CLAIMED RESULT / verification accepted
  -> protected handler output generated privately
  -> ACTUAL SETTLEMENT/FINALITY
  -> public response commit or flush
  -> RESOURCE/OUTCOME DELIVERY
```

## Core invariant

> Protected output may be generated before settlement only while it remains
> non-public and discardable. Public commit, flush, or delivery requires a
> matching authoritative settled event that already existed at that decision
> point.

Three middleware events remain distinct:

```text
handler executed
!= protected body generated
!= protected body became visible to the client
```

Payment admission and finality also remain distinct:

```text
verify accepted
!= settle succeeded
```

## Opt-in contract

```json
{
  "stage": "POLICY DECISION",
  "key": "requires_settlement_gated_delivery",
  "value": {
    "required": true,
    "protected_body_must_remain_discardable_until_settled": true,
    "implementation_provenance_required": true
  },
  "operation_id": "operation-1"
}
```

`implementation_provenance_required` is optional. When enabled, the trace must
include an authoritative `implementation_artifact` record naming the language,
artifact, and either a revision/commit or version. A global requirement is a
minimum for every operation; an operation-specific contract cannot weaken it.

Provenance identifies what was tested. It never proves conformance merely
because a version string looks new.

## Evidence vocabulary

### Verification

```json
{
  "stage": "CLAIMED RESULT",
  "key": "payment_verification_status",
  "value": "verified",
  "authorization_id": "authorization-1",
  "payment_id": "payment-1"
}
```

### Protected response state

```json
{
  "stage": "RESOURCE/OUTCOME DELIVERY",
  "key": "protected_response_state",
  "value": "buffered",
  "authorization_id": "authorization-1",
  "payment_id": "payment-1"
}
```

Private states include `generated`, `buffered`, `held`, `private`, and `staged`.
Public states include `committed`, `flushed`, `published`, and `client_visible`.
Safe terminal failure states include `discarded`, `invalidated`, `revoked`, and
`absent`.

A retained `buffered`, `generated`, `held`, `staged`, `active`, or `retained`
state after failed settlement is not itself proof that bytes leaked, but it
leaves protected material reusable and is reported as a high-severity gate
failure.

### Finality

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "payment_status",
  "value": "settled",
  "authoritative": true,
  "authorization_id": "authorization-1",
  "payment_id": "payment-1"
}
```

Only authoritative terminal evidence closes the gate. A verified payment whose
protected body remains private but whose finality is absent remains
`UNRESOLVED`; it is not promoted to a completed safe lifecycle.

### Protected delivery

```json
{
  "stage": "RESOURCE/OUTCOME DELIVERY",
  "key": "delivery_status",
  "value": "delivered",
  "authorization_id": "authorization-1",
  "payment_id": "payment-1"
}
```

An HTTP 402 or another unprotected error response uses a different key, such as
`error_response_status`, and is not treated as protected business delivery.

### Implementation provenance

```json
{
  "stage": "RECONCILIATION",
  "key": "implementation_artifact",
  "value": {
    "language": "java",
    "artifact": "org.x402:x402 PaymentFilter",
    "commit": "5df361d591fd3df74eb363296347b2ed57e8f413"
  },
  "authoritative": true,
  "operation_id": "operation-1"
}
```

## Identity and attempt isolation

Settlement, response state, and delivery are matched through typed
`authorization_id` and `payment_id`. Equal text in different fields is not a
cross-field match. `attempt_id` may correlate transport evidence, but
attempt-only correlation remains `UNRESOLVED` for economic identity.

A fresh payment attempt with different typed identifiers is not a conflict just
because it belongs to the same business operation. It is evaluated separately.
A divergence requires either:

- one typed identifier matches while another conflicts; or
- the same `attempt_id` is reused with conflicting typed payment identity.

This prevents a safe recovery attempt from being mistaken for identity
corruption while still failing closed on partial-ID crossover.

## Temporal and disposal rules

Ordering uses `observed_at` when all compared events expose usable absolute
timestamps; otherwise it uses trace order.

```text
protected delivery
-> settlement succeeds later
```

This remains a gate violation. Eventual payment may balance the economics but
cannot retroactively authorize an earlier disclosure.

```text
protected delivery
-> settlement fails later
```

This is critical: the protected response was public before the system learned
that payment had failed.

```text
settlement fails
-> protected body remains private
```

The trace must show disposal or a later settlement before publication. If the
body was already public, Astra reports the stronger commit/delivery violations
and does not add a redundant disposal finding for an irreversible disclosure.
If settlement later succeeds before any publication, the staged body may be
released without a redundant discard step.

## Findings

| Code | Meaning |
|---|---|
| `SETTLEMENT_GATED_DELIVERY_CONTRACT_INVALID` | The contract does not explicitly require discardable protected output until settlement. |
| `PAYMENT_VERIFICATION_EVIDENCE_MISSING` | No successful verification event is supplied. |
| `SETTLEMENT_GATE_FINALITY_EVIDENCE_MISSING` | Verification exists but no matching authoritative terminal finality or public decision is present. |
| `SETTLEMENT_GATE_IMPLEMENTATION_PROVENANCE_MISSING` | Required authoritative build provenance is absent or incomplete. |
| `SETTLEMENT_GATE_IDENTITY_UNRESOLVED` | Evidence is correlated only weakly, such as by attempt ID without typed payment identity. |
| `SETTLEMENT_GATE_IDENTITY_DIVERGENCE` | Evidence partially matches or reuses an attempt while conflicting on typed payment identity. |
| `PROTECTED_RESPONSE_GATE_EVIDENCE_MISSING` | The trace does not show whether protected output was buffered, committed, flushed, or discarded. |
| `PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT` | Protected output was committed or flushed before authoritative settlement success. |
| `PROTECTED_DELIVERY_PRECEDES_SETTLEMENT` | The protected business outcome reached the client before settlement success. |
| `SETTLEMENT_FAILED_AFTER_PROTECTED_DELIVERY` | Settlement failed after the protected response was already public. |
| `PROTECTED_DELIVERY_WITH_FAILED_SETTLEMENT` | Protected output became public despite an already-failed settlement. |
| `PROTECTED_DELIVERY_FINALITY_UNRESOLVED` | Protected output became public and no matching terminal finality is available. |
| `PROTECTED_BODY_DISPOSAL_EVIDENCE_MISSING` | A private body existed at terminal failure, but disposal/non-public resolution is not proven. |
| `PROTECTED_BODY_NOT_DISCARDED_AFTER_SETTLEMENT_FAILURE` | Protected output remained staged or reusable after failed settlement. |

The generic Astra core may additionally emit `DELIVERED_BUT_NOT_SETTLED` when a
protected result is delivered and the latest authoritative finality is not
settled.

## Fixture set

### Java pre-fix failure — divergent

`x402_java_delivery_before_failed_settlement.json`

```text
verify succeeds
handler generates body
real servlet response commits
client receives premium body
settlement fails
```

Expected gate findings:

- `PROTECTED_RESPONSE_COMMITTED_BEFORE_SETTLEMENT`;
- `PROTECTED_DELIVERY_PRECEDES_SETTLEMENT`;
- `SETTLEMENT_FAILED_AFTER_PROTECTED_DELIVERY`.

The generic core additionally emits `DELIVERED_BUT_NOT_SETTLED`. No disposal
finding is added after an already public leak.

### Delivery before eventual settlement — divergent

`x402_java_delivery_before_late_settlement.json`

The payment eventually settles, but protected content was visible first. Astra
reports ordering failure without calling it unpaid delivery.

### Buffered success — verified

`x402_java_buffered_until_settled.json`

```text
handler generates body
body remains private in buffer
settlement succeeds
body flushes
one protected response is delivered
```

### Buffered failure — verified

`x402_java_buffer_discarded_on_failure.json`

```text
handler generates body
body remains private in buffer
settlement fails
protected body is discarded
only an unprotected 402 error is returned
```

## Claim boundary

The Java defect and fix are publicly reproduced. Current x402 Java `main`
contains the buffering repair. This profile does not infer that a deployed jar is
fixed or vulnerable from its version label; it records and tests the actual
artifact revision.

The module does not require every merchant to execute business logic before
settlement. It permits that architecture when protected output remains private,
discardable, and non-delivered until finality.

## Commercial boundary

This becomes an Astra merchant-middleware assessment for:

- Java servlet filters;
- Express, Hono, and Next middleware;
- API gateways;
- streaming and inference responses;
- paid content systems;
- facilitator-integrated resource servers.

The deliverable is a cross-SDK red/green trace proving whether a protected body
can escape before the payment rail grants economic authority to publish it.
