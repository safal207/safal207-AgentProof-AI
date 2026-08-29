# Astra multi-leg settlement verification

Agent-payment flows increasingly contain more than one economically meaningful leg. A system may fund a delegate, reserve value, capture later, pay a merchant, refund, reclaim, or reconcile across separate ledgers.

Astra must not collapse an intermediate leg into a completed business outcome.

## Core invariant

```text
intermediate funding
  != merchant settlement
  != resource delivery
  != terminal reconciliation
```

The verifier accepts a protocol-neutral declaration:

```json
{
  "stage": "QUOTE/CHALLENGE",
  "key": "required_settlement_legs",
  "value": ["funding", "merchant"],
  "operation_id": "purchase-004"
}
```

Authoritative leg evidence can use either representation:

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "settlement_leg_status",
  "value": {"leg": "funding", "status": "settled"},
  "authoritative": true,
  "operation_id": "purchase-004"
}
```

or a protocol-specific key such as `funding_leg_status`.

## Findings

| Code | Meaning |
|---|---|
| `FUNDED_BUT_MERCHANT_UNSETTLED` | The funding leg is independently confirmed but merchant-settlement evidence is absent from the supplied trace. |
| `PARTIAL_SETTLEMENT_OUTCOME_UNRESOLVED` | At least one required economic leg is complete and at least one remains without authoritative completion evidence. |
| `PARTIAL_SETTLEMENT_CLAIMED_COMPLETE` | A status surface claims completion while a required leg remains unresolved. |
| `RECOVERY_ACTION_MISSING` | The status surface explicitly says no recovery action is required while a required leg remains unresolved. |

## Haven-AI mapping

The initial fixture is derived from the state boundary documented in Haven-AI issue `#2145`: an EIP-3009 funding leg could confirm while the merchant retry never happened, yet the status surface reported `payment_confirmed` and `next_action: none`.

Haven fixed the projection in PR `#2158` by deriving a server-side `funded_but_unsettled` state and a reachable `retry_original_x402_request` action after a grace window. Haven subsequently opened QA issue `#2159` for a deterministic live dev scenario that reproduces the crash shape and proves the resume path.

Astra's JSON fixture is a protocol-neutral conformance model. It does **not** claim that current Haven remains vulnerable, and it does not replace Haven's planned live environment test.

Public sources:

- <https://github.com/d-hinders/Haven-AI/issues/2145>
- <https://github.com/d-hinders/Haven-AI/pull/2158>
- <https://github.com/d-hinders/Haven-AI/issues/2159>

## Claim boundary

A missing leg means no authoritative completion evidence for that leg is present in the supplied trace. It does not prove that the leg never completed in an unobserved system.

The strongest next probe is therefore a controlled live crash/restart run:

1. authorize the logical purchase;
2. confirm exactly one funding leg;
3. terminate the agent before merchant retry;
4. verify the status surface requests recovery rather than claiming completion;
5. resume the same operation without re-authorizing;
6. prove exactly one funding movement, exactly one merchant settlement, delivery, and terminal reconciliation.
