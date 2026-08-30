# Astra case study: Stellar settlement succeeded, card delivery remained unobserved

## Public incident

ASG Card issue `ASGCompute/asgcard-public#17` reports a virtual-card creation request that started at `2026-08-30T01:15:00Z`.

Observed by the caller:

- requested card load: `$25`;
- client result: HTTP `502`;
- no card details returned;
- card-list and history calls timed out;
- no retry was attempted because another payment might duplicate the charge.

The ASG Card README describes the Stellar flow as:

```text
402 challenge
  -> signed Stellar USDC payment
  -> facilitator verifies and settles
  -> API calls the card issuer
  -> card details returned
```

Its published pricing is `$10` for card creation plus `3.5%` on the load. For a `$25` load, that contextual formula is:

```text
10.00 + 25.00 * 1.035 = 35.875
```

The exact challenge body is not public, so the formula is context, not authorization evidence.

## Independent read-only finality probe

A GitHub Actions job queried public Stellar Horizon endpoints with no credentials, secrets, signing, or submission capability.

At `2026-08-30T01:15:39Z`, 39 seconds after the reported start:

- transaction `2b02ade834ffaa839cdfeca4409af049121a014abe0cf5bab95801012d2fe134` succeeded;
- operation type: `invoke_host_function`;
- the reported wallet was debited `35.8800000 USDC`;
- destination `GAHYHA55RTD2J4LAVJILTNHWMF2H2YVK5QXLQT3CHCJSVET3VRWPOCW6` was credited the same amount.

That establishes successful economic movement. It does not, by itself, establish card issuance or delivery.

At `2026-08-30T02:01:49Z`, the wallet received a separate `9.7000000 USDC` credit. The public evidence does not bind that credit to the failed card-create operation. Astra therefore records it as an **unattributed post-incident credit**, not as a refund or completed reconciliation.

## Adapter lesson: one ledger view was insufficient

A naive adapter that selected only classic outgoing `payment` records found zero candidate outgoing USDC payments for the incident transaction. Horizon represented the relevant operation as `invoke_host_function`.

The value movement became visible in the transaction effects:

```text
account_debited  35.8800000 USDC
account_credited 35.8800000 USDC
```

Therefore, Astra's Stellar adapter rule is:

> Absence from the account payment stream is not proof of non-settlement when the operation executes through Soroban. Resolve the transaction effects or contract events before assigning finality.

This is an observability boundary, not a claim that Horizon is defective. Different ledger views expose different parts of the same successful transaction.

## State graph

```text
REQUEST: create $25 virtual card
  -> PAYMENT ATTEMPT: payment submitted
  -> CLAIMED RESULT: HTTP 502 / failed
  -> ACTUAL SETTLEMENT: 35.88 USDC settled on Stellar Mainnet
  -> RECEIPT: absent from the public trace
  -> RESOURCE/OUTCOME DELIVERY: no card details observed
  -> RECONCILIATION: no operation-bound terminal record observed
```

## Astra verdict

The offline fixture `stellar_asgcard_settled_without_delivery.json` expects:

```text
CLAIMED_FAILED_BUT_SETTLED
SETTLED_BUT_NOT_DELIVERED
verdict: DIVERGED
```

`SETTLED_BUT_NOT_DELIVERED` is an evidence-scoped statement. It means no confirming delivery event exists in the supplied trace after authoritative settlement. It does **not** prove that the issuer never created a card or that no internal ASG record exists.

## Why the later credit does not close the operation

A credit becomes reconciliation evidence only when it is causally bound to the original operation by a stable operation/payment identity or another authoritative linkage. Matching account and nearby time are insufficient.

The required reconciliation record should distinguish at least:

```text
card issued and attached to account
card issued but client response lost
issuance failed and full refund settled
issuance failed and partial refund/fee retained
manual compensation unrelated to refund
still unresolved
```

## Next technical probe

The smallest decisive provider-side probe is read-only:

1. resolve the card-create operation by wallet, transaction hash, and timestamp;
2. retrieve the downstream issuer request/result and durable card record;
3. bind any refund or compensation transaction to that same operation ID;
4. return one terminal status without asking the caller to create a second payment.

No new card-create request should be made until the first economic operation is reconciled.
