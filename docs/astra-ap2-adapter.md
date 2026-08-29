# Astra AP2 terminal-commitment adapter

This adapter maps the AP2 human-not-present checkout boundary into Astra's
protocol-neutral causal/economic state graph.

Public source:

- AP2 issue #308:
  https://github.com/google-agentic-commerce/AP2/issues/308

## Evidence boundary

The public report is scoped to the sample at commit `e1ea56d`, one controlled
configuration, and a deliberately unavailable PSP endpoint. It establishes
that:

1. the sample persisted `token.used = true`;
2. an `order_id` was allocated;
3. both writes occurred before the PSP settlement call;
4. the PSP call failed;
5. no receipt or transaction hash was produced; and
6. no compensating store write was observed on the exception path.

It does **not** establish loss of funds. Broadcast was disabled and no transfer
was submitted. The report also did not execute the same-token retry; the
`token_already_used` outcome is derived from the sample's refusal path.

Astra preserves that distinction. Local store evidence can be authoritative
about local token/order state while remaining non-authoritative about economic
settlement.

## State mapping

```text
AP2 mandate / checkout identity
  -> PAYMENT ATTEMPT
  -> terminal local commitment (token used, order allocated)
  -> ACTUAL PSP SETTLEMENT / FINALITY
  -> RECEIPT
  -> RESOURCE DELIVERY
  -> RECONCILIATION
```

The adapter emits:

- `requires_settlement_before_terminal_commitment = true` as an explicit
  contract declaration;
- `terminal_commitment` events for terminal token/order state;
- independent PSP, chain, or controlled-harness evidence as authoritative
  `payment_status`;
- one stable `operation_id` across interruption and resume;
- the original `authorization_id` when recovery reuses the same approved
  obligation; and
- `expected_settlement_count = 1` for an ordinary checkout.

## New invariants

| Code | Meaning |
|---|---|
| `TERMINAL_COMMITMENT_WITHOUT_SETTLEMENT` | Token/order state is terminal although authoritative settlement completion is absent. |
| `TERMINAL_COMMITMENT_PRECEDES_SETTLEMENT` | Terminal local state was persisted before settlement completion, leaving an interruption window. |
| `NONSETTLED_OPERATION_MARKED_NONRETRYABLE` | The operation is explicitly blocked or closed although settlement did not complete. |

These checks are opt-in. A protocol that intentionally supports a terminal
reservation before settlement should not declare this contract; it should
instead expose its reservation, expiry, retry, and reconciliation states
explicitly.

## Red fixture

`fixtures/astra/ap2_token_consumed_psp_failed.json` represents the reported
failure boundary:

```text
same operation
  -> payment attempt
  -> token used + order allocated
  -> authoritative not_settled
  -> token_already_used
  -> DIVERGED
```

Expected findings:

- `TERMINAL_COMMITMENT_WITHOUT_SETTLEMENT`
- `NONSETTLED_OPERATION_MARKED_NONRETRYABLE`

## Green fixture

`fixtures/astra/ap2_safe_resume_reconciled.json` is the portable recovery
contract, not a claim about current AP2 behavior:

```text
same operation_id
same authorization_id
  -> attempt 1: authoritative not_settled, retry_settlement
  -> attempt 2: exactly one authoritative settlement
  -> token/order terminal state after settlement
  -> matching receipt
  -> delivered resource
  -> terminal reconciliation
  -> VERIFIED
```

This distinguishes a safe resume from silently creating a new authorization or
a second business obligation.

## Claim boundary

Astra proves consistency only across the supplied evidence. A missing
settlement, receipt, delivery, or reconciliation event means that confirming
evidence is absent from the trace. It does not prove that the event could not
exist in an unobserved system.
