# Astra case study: Stellar value movement confirmed, operation binding unresolved

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

## Independent read-only ledger probe

A GitHub Actions job queries public Stellar Horizon endpoints with no credentials, secrets, signing, or submission capability.

At `2026-08-30T01:15:39Z`, 39 seconds after the reported start:

- transaction `2b02ade834ffaa839cdfeca4409af049121a014abe0cf5bab95801012d2fe134` succeeded;
- operation type: `invoke_host_function`;
- the reported wallet was debited `35.8800000 USDC`;
- destination `GAHYHA55RTD2J4LAVJILTNHWMF2H2YVK5QXLQT3CHCJSVET3VRWPOCW6` was credited the same amount.

That establishes authoritative ledger value movement. It does not, by itself, establish which business operation caused the transaction, whether a card was issued, or whether delivery occurred.

## Correlation is not causal binding

The transaction is **strongly contextually correlated** with the reported operation:

- it debits the public wallet named in the incident;
- it lands 39 seconds after the reported start;
- `35.88 USDC` matches the reported balance change and rounded public pricing formula.

But the public evidence does not contain:

- the exact 402 challenge or signed payment envelope;
- a provider request ID or stable operation ID;
- a merchant receipt binding the transaction to card issuance.

Astra therefore records the transaction as authoritative **candidate settlement evidence** without assigning it to the card-create `operation_id`.

This distinction prevents a nearby transfer from becoming operation-level truth merely because its wallet, amount, and time look persuasive.

## Adapter lesson: one ledger view was insufficient

A naive adapter that selected only classic outgoing `payment` records found zero candidate outgoing USDC payments for the incident transaction. Horizon represented the relevant operation as `invoke_host_function`.

The value movement became visible in the transaction effects:

```text
account_debited  35.8800000 USDC
account_credited 35.8800000 USDC
```

Astra's Stellar adapter rule is:

> Absence from the account payment stream is not proof of non-settlement when the operation executes through Soroban. Resolve the transaction effects or contract events before assigning ledger finality.

The live workflow asserts both the Soroban operation type and this ledger-view boundary. This is not a claim that Horizon is defective; different ledger views expose different parts of the same transaction.

## State graph

```text
REQUEST: create $25 virtual card
  -> PAYMENT ATTEMPT: payment-gated operation submitted
  -> CLAIMED RESULT: card-create operation failed with HTTP 502
  -> ACTUAL SETTLEMENT/FINALITY: 35.88 USDC candidate settlement confirmed
  -> RECEIPT / OPERATION BINDING: absent from the public trace
  -> RESOURCE/OUTCOME DELIVERY: no card details observed by the caller
  -> RECONCILIATION: unresolved
```

The fixture records the HTTP result as generic `outcome_status=failed`. The ledger transaction uses `candidate_payment_status=settled`, remains authoritative about value movement, and deliberately has no `operation_id`.

## Astra verdict

The offline fixture `stellar_asgcard_settlement_binding_unresolved.json` expects:

```text
SETTLEMENT_OPERATION_BINDING_UNRESOLVED
verdict: DIVERGED
```

The finding means:

> An authoritative ledger settlement exists in the supplied trace, but the evidence does not authoritatively bind it to the reported card-create operation.

Until that binding exists, Astra does **not** emit either:

```text
CLAIMED_FAILED_BUT_SETTLED
SETTLED_BUT_NOT_DELIVERED
```

Those stronger findings become valid only after a receipt, authorization, provider record, or equivalent authoritative linkage connects the payment transaction to the operation.

## Why the later credit does not close the operation

At `2026-08-30T02:01:49Z`, the wallet received a separate `9.7000000 USDC` credit. The public evidence does not bind that credit to the card-create operation. Astra records it as an **unattributed post-incident credit**, not as a refund or completed reconciliation.

A credit becomes reconciliation evidence only when it is causally bound to the original operation by a stable operation/payment identity or another authoritative linkage. Matching account and nearby time are insufficient.

The provider-side terminal record should distinguish at least:

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
2. retrieve the x402 payment envelope or provider payment record;
3. retrieve the downstream issuer request/result and durable card record;
4. bind any refund or compensation transaction to that same operation ID;
5. return one terminal status without asking the caller to create a second payment.

No new card-create request should be made until the first economic operation is reconciled.
