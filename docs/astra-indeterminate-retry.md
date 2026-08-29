# Astra indeterminate-settlement retry contract

This contract covers the retry window where an HTTP client, gateway, or
facilitator cannot tell whether a payment settled before the response was lost.

Public sources:

- Obolus x402 gateway issue #17:
  https://github.com/geekinasuit/obolus/issues/17
- mppx x402 CLI support PR #850:
  https://github.com/wevm/mppx/pull/850

## State loop

```text
PAYMENT ATTEMPT
  -> CLAIMED RESULT: indeterminate
  -> ACTUAL SETTLEMENT/FINALITY: unresolved
  -> RETRY PAYMENT ATTEMPT
```

A transport failure is not evidence that money did not move. A fresh payment
authorization is therefore unsafe until the previous authorization's economic
state is resolved.

## Why nonce replay protection is not enough

Reusing one EIP-3009 authorization keeps the same nonce. The token contract can
make settlement idempotent for that identity.

A new top-level client call can instead create a fresh nonce. The second nonce
is a distinct, independently spendable authorization. On-chain replay
protection correctly accepts it because it is not a replay of the first
payment.

The idempotency key Astra needs is therefore the stable business
`operation_id`, with payment nonce / `authorization_id` recorded beneath it.
Request-body equality is not a safe substitute: two buyers may legitimately
request identical content.

## Opt-in evidence contract

Adapters declare:

```json
{
  "stage": "QUOTE/CHALLENGE",
  "key": "requires_resolution_before_fresh_authorization_after_indeterminate",
  "value": {
    "required": true,
    "same_authorization_idempotent": true
  }
}
```

`same_authorization_idempotent` must be asserted by the integration; Astra does
not assume every payment rail makes credential resubmission economically
idempotent.

Each payment attempt should expose:

- one stable `operation_id`;
- an `attempt_id` for the transport/execution attempt;
- an `authorization_id` for the spend authorization or nonce; and
- a `payment_id` where the protocol exposes one.

## Resolution rule

Only independently designated authoritative finality can clear the retry
ambiguity:

- authoritative `not_settled`, `failed`, `rejected`, `expired`, or `voided`
  allows a fresh authorization;
- authoritative `settled` makes a fresh authorization critical;
- a facilitator/client claim such as `failed` without authoritative finality
  leaves the payment unresolved;
- absence of finality also remains unresolved rather than silently permitting a
  new payment; and
- resubmitting the same identity is accepted only when the integration declares
  that identity economically idempotent, including after settlement was already
  observed.

The unresolved state is checked at every adjacent retry boundary. Reusing one
idempotent authorization does not erase the ambiguity before a later fresh
authorization.

## Findings

| Code | Meaning |
|---|---|
| `FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT` | A new spend authorization was created before the prior payment was authoritatively resolved. This is an unsafe retry contract, not proof of a duplicate charge. |
| `FRESH_AUTHORIZATION_AFTER_CONFIRMED_SETTLEMENT` | A new spend authorization was created after the previous payment was already confirmed settled. |
| `INDETERMINATE_RETRY_IDEMPOTENCY_UNPROVEN` | The same identity was reused, but the integration did not establish idempotent settlement for that identity. |
| `RETRY_PAYMENT_IDENTITY_UNRESOLVED` | The trace cannot compare the original and retry payment identities. |

Confirmed duplicate settlement remains the stronger existing Astra finding
`RETRY_DUPLICATE_PAYMENT`, which requires more than one distinct authoritative
settlement for one operation.

## Red fixture

`x402_indeterminate_fresh_authorization.json` models:

```text
operation O
  -> authorization A / nonce A
  -> settlement indeterminate
  -> no authoritative settlement status check
  -> authorization B / nonce B
  -> DIVERGED
```

It intentionally does not assert that either payment ultimately settled.

## Green fixtures

`x402_indeterminate_resolved_then_reauthorized.json` requires an authoritative
`not_settled` result for authorization A before authorization B is created.
Authorization B then settles exactly once and is tied to receipt, delivery, and
terminal reconciliation.

`x402_indeterminate_same_authorization_resume.json` exercises the alternative
safe path: retry the same authorization identity on a rail explicitly declared
idempotent for that identity, observe one settlement, then reconcile delivery.

## Claim boundary

The Obolus source documents the failure analysis and reference-client behavior,
but does not report a completed real double-charge experiment. Astra therefore
reports the unsafe causal contract separately from a confirmed duplicate
payment. Real chain, facilitator, or PSP evidence is required for the stronger
claim.
