# Astra payment-session accounting

A wallet settlement, an authorization ceiling, and a payment-session debit can
all be correct while describing different economic facts. This profile verifies
those ledgers independently and prevents an application from presenting chain
spend as session-budget consumption—or the reverse.

## Public evidence

The merged AgentCore x402 `upto` tutorial documents the boundary explicitly:

```text
buyer authorizes a ceiling
AgentCore payment session is debited by that ceiling
seller settles the actual metered amount at or below the ceiling
unused difference is not credited back to the session
```

Its published example is:

```text
session budget before:             50000 atomic USDC ($0.05)
authorized and session-debited:     3303 atomic USDC
settled on Base mainnet:             3003 atomic USDC
session budget after:               46697 atomic USDC
```

Public source: `awslabs/agentcore-samples#1967`, authored publicly by
`@ggChris2`. No personal email is asserted.

This is documented behavior, not a defect. The verification problem appears
when software collapses all three amounts into one `amount_spent`, derives
remaining policy capacity from wallet settlement alone, or assumes session
debit proves the amount transferred to the merchant.

## State edge

```text
QUOTE/CHALLENGE
  -> MANDATE/AUTHORIZATION (maximum)
  -> POLICY DECISION / SESSION DEBIT
  -> PAYMENT ATTEMPT
  -> ACTUAL SETTLEMENT/FINALITY (economic transfer)
  -> RECEIPT
  -> RECONCILIATION / SESSION CREDIT AND BALANCE
```

The actual system may record the session debit while signing—before the seller
settles. Astra does not impose an artificial event order between the two
ledgers. It requires causal identity, authority, asset consistency, and exact
arithmetic.

## Core invariant

> Authorized ceiling, independent settlement, session debit, session credit,
> and remaining policy capacity are distinct facts with distinct authorities.
> None may be inferred from another unless the accounting contract explicitly
> declares the relationship.

## Accounting contract

An integration declares one of three debit bases and one remainder policy:

```json
{
  "stage": "POLICY DECISION",
  "key": "payment_session_accounting_contract",
  "value": {
    "required": true,
    "debit_basis": "authorized_ceiling",
    "remainder_policy": "not_credited"
  },
  "session_id": "session-a"
}
```

Supported debit bases:

- `authorized_ceiling` — session debit equals the amount the buyer authorized;
- `actual_settlement` — session debit equals independently observed finality;
- `explicit_provider_amount` — an authoritative provider-specific amount is the
  ledger basis and must be supplied separately.

Supported remainder policies:

- `not_credited` — session credit is zero;
- `credited` — the difference between session debit and actual settlement is
  credited back;
- `external_reconciliation` — another authoritative process closes the
  remainder and must expose a completed reconciliation status.

A global contract and a session-specific contract may coexist only when they
declare the same semantics. Conflicting declarations fail closed rather than
letting the narrower scope silently reinterpret the ledger.

## Exact amount evidence

Every amount is an exact non-negative integer minor-unit record:

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "settled_amount_minor",
  "value": {
    "amount_minor": "3003",
    "asset": "USDC"
  },
  "authoritative": true,
  "session_id": "session-a",
  "operation_id": "operation-a",
  "authorization_id": "authorization-a",
  "payment_id": "payment-a"
}
```

Binary floating-point values, negative amounts, and fractional minor units are
rejected. Asset identifiers are normalized for comparison but remain separate
from amount.

The core evidence keys are:

- `authorized_ceiling_minor` — authoritative signed maximum;
- `settled_amount_minor` — authoritative economic finality;
- `session_debit_minor` — authoritative policy-ledger debit;
- `provider_debit_amount_minor` — required only for
  `explicit_provider_amount`;
- `session_credit_minor` — authoritative credit/remainder adjustment;
- `session_remaining_before_minor` and `session_remaining_after_minor`;
- `claimed_session_spend_minor` and `claimed_session_remaining_minor` —
  application/UI/agent claims to compare against authoritative accounting;
- `session_remainder_reconciliation_status` — authoritative completion signal
  for `external_reconciliation`.

## Causal identity

Amounts are compared only when they share:

- `session_id`;
- `operation_id`;
- normalized asset;
- at least one common typed `authorization_id` or `payment_id`, with no conflict.

`authorization_id="abc"` and `payment_id="abc"` are different identity
namespaces. Equal text does not create a cross-namespace match.

Wrong-session, wrong-operation, wrong-asset, and missing typed identity evidence
are reported separately. A transaction close in time or inside the same wallet
is not enough.

## Arithmetic

### Ceiling debit, no credit

AgentCore's documented `upto` semantics:

```text
session_debit = authorized_ceiling
session_credit = 0
net_session_spend = session_debit
remaining_after = remaining_before - session_debit
```

Wallet spend remains:

```text
wallet_spend = actual_settlement
```

These two spend values intentionally differ.

### Actual-settlement debit

```text
session_debit = actual_settlement
session_credit = 0
net_session_spend = actual_settlement
```

### Credited remainder

```text
expected_credit = max(session_debit - actual_settlement, 0)
net_session_spend = session_debit - session_credit
remaining_after = remaining_before - session_debit + session_credit
```

### External reconciliation

A completed authoritative `session_remainder_reconciliation_status` is required.
Astra does not invent the external credit amount; if before/after balances or a
credit are supplied, their arithmetic is still checked.

## Findings

### Contract and evidence

- `PAYMENT_SESSION_ACCOUNTING_CONTRACT_MISSING`
- `PAYMENT_SESSION_ACCOUNTING_CONTRACT_INVALID`
- `PAYMENT_SESSION_ACCOUNTING_CONTRACT_CONFLICT`
- `AUTHORIZED_CEILING_EVIDENCE_MISSING`
- `ACTUAL_SETTLEMENT_AMOUNT_EVIDENCE_MISSING`
- `SESSION_DEBIT_EVIDENCE_MISSING`
- `EXPLICIT_PROVIDER_AMOUNT_EVIDENCE_MISSING`
- `SESSION_REMAINDER_EVIDENCE_MISSING`
- `SESSION_REMAINING_BALANCE_EVIDENCE_MISSING`

### Authority, scope, and identity

- `SESSION_ACCOUNTING_AMOUNT_INVALID`
- `SESSION_ACCOUNTING_EVIDENCE_CONFLICT`
- `SESSION_ACCOUNTING_IDENTITY_UNRESOLVED`
- `SESSION_ACCOUNTING_SESSION_MISMATCH`
- `SESSION_ACCOUNTING_OPERATION_MISMATCH`
- `SESSION_ACCOUNTING_ASSET_MISMATCH`

### Economic divergence

- `SETTLEMENT_EXCEEDS_AUTHORIZED_CEILING`
- `SESSION_DEBIT_EXCEEDS_AUTHORIZED_CEILING`
- `SESSION_DEBIT_BASIS_MISMATCH`
- `SESSION_REMAINDER_POLICY_MISMATCH`
- `SESSION_REMAINING_BALANCE_MISMATCH`
- `CLAIMED_SESSION_SPEND_MISMATCH`
- `CLAIMED_SESSION_REMAINING_MISMATCH`

Settlement or session debit above the authorization ceiling is critical. A
correctly declared ceiling-debit/no-credit policy is verified even when wallet
settlement is lower.

## Fixture set

### AgentCore `upto` claim divergence

`agentcore_upto_settlement_claimed_as_session_spend.json`

```text
ceiling = 3303
settlement = 3003
session debit = 3303
credit = 0
authoritative remaining = 46697
application claims spend = 3003
application claims remaining = 46997
```

Expected:

```text
CLAIMED_SESSION_SPEND_MISMATCH
CLAIMED_SESSION_REMAINING_MISMATCH
DIVERGED
```

This does not claim AgentCore debited incorrectly. The authoritative AgentCore
ledger is internally consistent; the application claim is wrong.

### AgentCore ceiling-debit lifecycle

`agentcore_upto_ceiling_debit_reconciled.json`

The same public semantics are represented correctly and produce `VERIFIED`.

### Actual-settlement debit lifecycle

`actual_settlement_debit_reconciled.json`

A different provider contract debits only the final settled amount and also
produces `VERIFIED`.

### Over-ceiling settlement

`settlement_and_session_debit_over_ceiling.json`

Settlement and debit both exceed the signed maximum, producing critical
settlement and session-ledger findings.

## Claim boundary

The AgentCore tutorial proves its declared accounting semantics and includes
real Base-mainnet settlement values. It does not prove that a production client
misreported its budget. The red claim fixture is an adversarial application
scenario built from the documented ledger difference.

Astra reports ledger relationships only from supplied authoritative evidence. A
chain transaction proves wallet movement, not session consumption. A session
balance delta proves policy capacity usage, not merchant settlement. A dashboard
claim proves neither.

## Controlled live probe

For one low-value `upto` call:

1. record the 402 ceiling and signed proof identity;
2. record session remaining budget before `ProcessPayment`;
3. record the independent Base settlement amount;
4. record the session debit and remaining budget after the call;
5. preserve payment response, receipt, delivered output, and operation ID;
6. verify both ledgers under the declared `authorized_ceiling/not_credited`
   contract.

The tutorial warns that the current `upto` facilitator is mainnet-only, so any
live probe must use explicit opt-in, a tightly funded wallet, and the smallest
available call. Source-only fixtures remain sufficient for CI conformance.

## Commercial boundary

This module is not a policy engine and does not decide the session limit. It
independently verifies whether wallet spend, policy-ledger consumption, and
remaining authorization capacity are each represented truthfully after the
payment decision.
