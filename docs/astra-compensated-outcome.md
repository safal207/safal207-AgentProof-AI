# Astra compensated terminal outcome

`COMPENSATED` is a terminal Astra verdict for a failed paid operation whose economic loss was later closed by independently verified recovery.

It is deliberately distinct from `VERIFIED`:

```text
VERIFIED
  the intended paid outcome completed correctly

COMPENSATED
  the intended outcome failed, but full economic restoration is proven

DIVERGED
  a material inconsistency or unremediated loss remains

UNRESOLVED
  only non-terminal observability or completeness gaps remain
```

A compensated report keeps every historical failure finding. The verdict changes the current economic-loss assessment; it does not rewrite history.

## State contract

```text
ACTUAL SETTLEMENT/FINALITY
  -> PROVIDER / OUTCOME FAILURE
  -> REFUND / REVERSAL FINALITY
  -> AUTHORITATIVE OPERATION BINDING
  -> TERMINAL RECONCILIATION
```

The evidence must occur in that order in the supplied trace.

## Initial compensable findings

The first profile is intentionally narrow:

- `CLAIMED_FAILED_BUT_SETTLED`
- `SETTLED_BUT_NOT_DELIVERED`
- `SETTLED_FULFILLMENT_FAILED`

Any other finding keeps the trace out of `COMPENSATED` until that failure class receives an explicit recovery model. For example, a refund does not automatically compensate replay, duplicate payment, over-capture, session crossover, or unresolved attribution.

## Required evidence

Every affected operation must have:

1. one non-empty stable `operation_id`;
2. exactly one authoritative original settled payment ID;
3. authoritative `settled_amount_minor` for that payment;
4. authoritative `settlement_asset` for that payment;
5. one or more independently finalized refund payments;
6. authoritative `refunded_amount_minor` for every refund;
7. authoritative `refund_asset` exactly matching the original asset;
8. authoritative `refund_operation_binding` linking every counted refund to the original operation;
9. no unresolved or contradictory confidence marker on the terminal binding;
10. authoritative reconciliation status `refunded`, `fully_refunded`, or `compensated` after the binding;
11. refund total exactly equal to the original settled amount.

The classifier rejects under-refunds, over-refunds, wrong-asset transfers, ambiguous bindings, missing operation IDs, missing reconciliation, and causal-order violations.

## Multiple operations

A trace is `COMPENSATED` only when every operation represented by a finding is fully compensated. One recovered operation cannot hide a second unresolved loss.

## Why correlation is insufficient

A transfer back to the payer is not automatically a refund. Matching wallet, amount, time, memo, or counterparty can produce a useful candidate, but the transfer must still be authoritatively bound to the original business operation.

This is especially important for public-chain investigation: ledger finality can prove that value moved, but it cannot by itself prove why it moved.

## Fixture set

`fixtures/astra_compensation/` contains deterministic cases for:

| Fixture | Verdict |
|---|---|
| `full_refund_compensated.json` | `COMPENSATED` |
| `partial_refund_diverged.json` | `DIVERGED` |
| `wrong_asset_diverged.json` | `DIVERGED` |
| `unbound_refund_diverged.json` | `DIVERGED` |
| `refund_without_terminal_reconciliation.json` | `DIVERGED` |
| `mixed_operations_diverged.json` | `DIVERGED` |

All fixtures run through the standard command:

```bash
python scripts/run_astra_fixtures.py
```

## Public ASG Card boundary

The public ASG Card evidence remains insufficient for `COMPENSATED`. The visible later credit is only a candidate movement: no authoritative provider refund record or stable operation-to-refund binding has been published. The public fixture therefore remains `UNRESOLVED` at the settlement-attribution boundary.
