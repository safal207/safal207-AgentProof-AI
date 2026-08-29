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

## Haven-AI red/green evidence

The divergent fixture is derived from the state boundary documented in Haven-AI issue `#2145`: an EIP-3009 funding leg could confirm while the merchant retry never happened, yet the status surface reported `payment_confirmed` and `next_action: none`.

Haven fixed the projection in PR `#2158` by deriving a server-side `funded_but_unsettled` state and a reachable `retry_original_x402_request` action after a grace window. PR `#2183` then added a deterministic Base-Sepolia QA scenario for the crash and resume path.

The first post-merge live run reproduced a deployment/configuration mismatch: the intended dev-only zero-minute grace override had not taken effect, so the scenario still observed `payment_confirmed` / `none`. Cleanup swept the intentional 0.001-USDC residual on both bounded attempts.

After the Base-Sepolia-only deployment configuration was corrected, live QA run `33209465916` passed all 14 scenarios on the first attempt. The crash/resume leg demonstrated that funding without merchant retry became recoverable, `resumeX402Payment` completed the original operation, 0.001 USDC moved treasury to merchant, and the delegate returned to its baseline.

Astra therefore ships a paired oracle:

- `x402_funded_but_merchant_unsettled.json` — the divergent pre-recovery state, expected `DIVERGED`;
- `x402_funded_resume_reconciled.json` — the normalized completed recovery chain, expected `VERIFIED`.

The green fixture uses sanitized identifiers and a protocol-neutral event model. It is supported by the public live-run summary but is not a verbatim export of Haven's private/runtime evidence.

Astra does **not** claim that current Haven remains vulnerable. The public evidence shows that the defect was fixed and the recovery path was subsequently verified live.

Public sources:

- <https://github.com/d-hinders/Haven-AI/issues/2145>
- <https://github.com/d-hinders/Haven-AI/pull/2158>
- <https://github.com/d-hinders/Haven-AI/issues/2159>
- <https://github.com/d-hinders/Haven-AI/pull/2183>

## Claim boundary

A missing leg means no authoritative completion evidence for that leg is present in the supplied trace. It does not prove that the leg never completed in an unobserved system.

A `VERIFIED` result means the supplied evidence contains all declared legs, one coherent final payment state, matching receipt/delivery evidence, and terminal reconciliation. It does not prove that an undeclared side payment or an unobserved external action cannot exist.

## Next portable probe

The Haven-specific live path is already covered upstream. Astra's next step is cross-system portability:

1. declare the required economic legs for another delegated-funding, bridge, escrow, or auth-capture flow;
2. preserve one logical `operation_id` across interruption and resume;
3. inject a crash after the first authoritative leg;
4. verify that no intermediate state is reported as completed;
5. resume without creating a second business obligation;
6. prove exact leg counts, delivery, and terminal reconciliation;
7. compare the normalized trace with the same red/green oracle contract.
