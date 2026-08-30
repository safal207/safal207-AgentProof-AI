# Astra provider-fulfillment verification

A payment can settle correctly while the purchased business outcome still fails, is created but not observed by the client, or is compensated by a refund that cannot be bound to the original operation.

This layer verifies the state edge:

```text
ACTUAL SETTLEMENT/FINALITY
  -> PROVIDER / ISSUER OUTCOME
  -> RESOURCE / CLIENT DELIVERY
  -> REFUND OR OTHER RECOVERY
  -> TERMINAL RECONCILIATION
```

It complements pre-execution policy and authorization controls. Those systems answer whether an action may execute. This verifier asks what happened after value moved.

## Evidence boundary

Provider and issuer outcomes are never inferred from HTTP status, missing rows, or absent delivery events. An adapter must supply an explicit event and mark it `authoritative=True` before Astra treats it as provider truth.

Examples:

- an authoritative issuer terminal record may establish `issuer_status=failed` or `issuer_status=issued`;
- a client transport record may explicitly establish `delivery_status=response_lost`;
- an independent ledger may establish that a refund movement occurred;
- wallet, amount, and timing correlation alone do not bind a refund to the original operation.

## Findings

### `SETTLED_FULFILLMENT_FAILED`

An operation-bound authoritative payment settlement exists and the authoritative provider or issuer outcome is explicitly failed, rejected, declined, or errored.

This finding can coexist with `SETTLED_BUT_NOT_DELIVERED`. The first explains the downstream failure; the second records that delivery did not complete in the supplied trace.

### `ISSUED_BUT_CLIENT_UNOBSERVED`

The provider authoritatively reports that the resource was issued, while an explicit client delivery record says the response was lost, disconnected, missing, unknown, or otherwise unobserved.

A missing delivery event alone is insufficient. Astra must not turn observability absence into a claim that the client failed to receive the result.

### `REFUND_OPERATION_BINDING_UNRESOLVED`

An authoritative refund movement exists, but the evidence does not authoritatively bind that movement to the failed operation. Contextual correlation is not terminal reconciliation.

A binding closes only when the reconciliation event is both:

- marked `authoritative=True`; and
- has a bound status such as `bound`, `verified`, or `authoritative`.

A self-declared `bound` string from a non-authoritative source does not close the gap.

## Fixture set

The fixtures under `fixtures/astra_fulfillment/` cover:

| Fixture | Expected result |
|---|---|
| `settled_issuer_failed.json` | `SETTLED_FULFILLMENT_FAILED` + `SETTLED_BUT_NOT_DELIVERED` |
| `issued_client_response_lost.json` | `ISSUED_BUT_CLIENT_UNOBSERVED` + `SETTLED_BUT_NOT_DELIVERED` |
| `refund_binding_unresolved.json` | `REFUND_OPERATION_BINDING_UNRESOLVED` |
| `issued_delivered_reconciled.json` | `VERIFIED` |

They are protocol-neutral synthetic conformance fixtures. They do not assert that any particular provider produced these outcomes.

## Provider adapter contract

For one operation, adapters should preserve:

- stable `operation_id`;
- payment and settlement identifier;
- authoritative settlement finality;
- issuer request ID or external resource ID;
- authoritative issuer terminal outcome;
- explicit client delivery state;
- refund or compensation payment ID, when applicable;
- authoritative operation-to-refund binding;
- terminal reconciliation status.

No new payment authorization should be created while the original operation remains economically unresolved.

## Relationship to the ASG Card public case

The public Stellar evidence in PR #14 proves value movement but not an authoritative operation-to-settlement binding or provider fulfillment result. Therefore the public incident fixture continues to emit only `SETTLEMENT_OPERATION_BINDING_UNRESOLVED`.

The stronger provider-fulfillment findings become valid only if a provider-side payment record, issuer record, durable resource record, refund record, or equivalent authoritative artifact supplies the missing linkage.
