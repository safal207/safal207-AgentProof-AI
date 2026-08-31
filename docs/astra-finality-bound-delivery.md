# Astra finality-bound delivery authority

A payment credential may be valid, a facilitator may accept it for verification,
and a response hash may be correct while the economic entitlement to release a
paid result never became final. This profile verifies the boundary between
payment admission, settlement finality, reusable cache state, replay, and
resource delivery.

## Public evidence

In the public `x402-foundation/x402#3234` discussion, the owner of
`x402toll.com`, `@SolomonisBlack`, reported a live defect found during an audit:

```text
payment verification succeeded
-> settlement failed
-> a replayable idempotency-cache entry remained
-> the same nonce was replayed
-> a paid response was returned without settlement
```

The defect was fixed, deployed, and converted into a regression test. The
response-provenance hash on the free responses was valid. That last fact is not
a weakness in response hashing; it proves that **response integrity and payment
finality are independent evidence dimensions**.

Public working channel: `x402-foundation/x402#3234`. No personal email is
asserted by this profile.

## State edge

```text
PAYMENT ATTEMPT
  -> CLAIMED RESULT / verification accepted
  -> ACTUAL SETTLEMENT/FINALITY
  -> RECONCILIATION / admission-cache state
  -> replay of the credential
  -> RESOURCE/OUTCOME DELIVERY
```

## Core invariant

> Successful payment verification is not reusable delivery authority. After
> authoritative settlement failure, verification-derived admission state must
> become non-authoritative before the same credential can be replayed. Paid
> delivery requires matching settlement finality or an explicitly separate,
> independently authorized non-payment entitlement.

This profile does not make `verify` useless. Verification remains a valid
precondition for attempting settlement. It simply cannot substitute for
settlement when deciding whether a paid resource may be released.

## Opt-in contract

An adapter declares:

```json
{
  "stage": "POLICY DECISION",
  "key": "requires_finality_bound_delivery_authority",
  "value": {
    "required": true,
    "verification_not_delivery_authority": true,
    "allow_non_payment_entitlement": false
  },
  "operation_id": "operation-1"
}
```

`allow_non_payment_entitlement` is optional and defaults to false. When enabled,
delivery may use a separate entitlement only if an authoritative
`non_payment_entitlement_status` event is present before delivery.

The entitlement option is a policy relaxation. Duplicate valid declarations at
the same operation scope are therefore combined strictly: non-payment entitlement
is allowed only when **every** declaration explicitly permits it. A later
permissive declaration cannot weaken an earlier strict contract.

## Evidence vocabulary

### Verification

```json
{
  "stage": "CLAIMED RESULT",
  "key": "payment_verification_status",
  "value": "verified",
  "authorization_id": "authorization-1"
}
```

Verification is admission evidence, not finality evidence.

### Settlement finality

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "payment_status",
  "value": "settled",
  "authoritative": true,
  "authorization_id": "authorization-1"
}
```

A failure value such as `failed`, `rejected`, `reverted`, `expired`, or
`not_settled` closes the payment attempt without granting paid delivery
authority.

### Verification-derived cache state

```json
{
  "stage": "RECONCILIATION",
  "key": "admission_cache_status",
  "value": "revoked",
  "authoritative": true,
  "authorization_id": "authorization-1"
}
```

Safe terminal states include `revoked`, `invalidated`, `expired`, `consumed`,
and `absent`. Unsafe reusable states include `active`, `available`, `cached`,
`verified`, and `replayable`.

Cache evidence must be authoritative. An application claim that a cache was
revoked cannot close the economic boundary by itself. The relevant state is the
last recognized authoritative state **before the first same-credential reuse**.
A revocation recorded only after replay or delivery cannot erase an earlier
active state at the decision point.

### Delivery authority basis

```json
{
  "stage": "RESOURCE/OUTCOME DELIVERY",
  "key": "delivery_authority_basis",
  "value": "settlement_finality",
  "authorization_id": "authorization-1"
}
```

Supported bases:

- `settlement_finality` — requires matching authoritative settlement before
  delivery;
- `verification_cache` — always invalid as paid-delivery authority after failed
  settlement;
- `non_payment_entitlement` — requires the contract opt-in and independent
  authoritative entitlement evidence.

An unknown basis is unresolved rather than implicitly trusted.

### Response provenance

A `response_provenance_status=verified` receipt may prove that the returned
content matches its declared hash or fixed point. It is intentionally ignored as
payment evidence. Astra may report a response as authentic while also reporting
that it was economically unauthorized.

## Identity binding

Replay and finality are linked through typed `authorization_id` and
`payment_id` fields. Equal strings in different fields are not silently treated
as one namespace. `attempt_id` is used only as a fallback when the payment
identity itself is absent from both compared events.

A later attempt with a fresh authorization is not labelled a replay of the
failed credential. It must be verified and settled under its own evidence.

## Chronology and authority precedence

The verifier evaluates each authoritative failed-finality boundary in trace
order. Evidence cannot authorize an earlier decision retroactively:

```text
failed settlement
-> replay
-> delivery
-> later settlement
```

The later settlement may explain eventual economic state, but it does not make
the earlier delivery authorized at the moment it occurred. A matching settlement
must exist after the failed boundary and no later than delivery.

A matching settlement observed before any replay closes the unsafe failed-state
window even if a separate cache-status event is absent. Otherwise the cache must
be proven safe before credential reuse.

## Findings

| Code | Meaning |
|---|---|
| `FINALITY_BOUND_DELIVERY_CONTRACT_INVALID` | The contract does not explicitly separate verification from delivery authority. |
| `PAYMENT_VERIFICATION_EVIDENCE_MISSING` | The required successful verification event is absent. |
| `SETTLEMENT_FINALITY_EVIDENCE_MISSING` | Verification exists but no matching authoritative terminal finality can be resolved. |
| `VERIFICATION_CACHE_STATUS_MISSING` | Failed settlement remains reusable because authoritative recognized cache state is absent before reuse. |
| `VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE` | Reusable verification-derived state remains active at the credential-reuse boundary. |
| `DELIVERY_AUTHORITY_BASIS_MISSING` | A replay produced delivery without declaring what authorized release. |
| `DELIVERY_AUTHORITY_FINALITY_UNRESOLVED` | The declared settlement or entitlement basis is unsupported by authoritative evidence existing at delivery time. |
| `VERIFICATION_USED_AS_DELIVERY_AUTHORITY` | The merchant explicitly released the result from verification-cache authority. |
| `REPLAY_PAYMENT_IDENTITY_UNRESOLVED` | A replay-like attempt is visible, but reuse of the failed credential cannot be proven. |
| `REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT` | The same credential produced delivery after settlement failure without finality or separate entitlement existing at delivery time. |

The generic Astra core may additionally report `DELIVERED_BUT_NOT_SETTLED`.
That finding states the economic outcome. The specialized findings explain the
admission mechanism that allowed it.

## Fixture set

### Live-defect class — divergent

`x402toll_verification_cache_free_delivery.json`

```text
verification succeeds
settlement fails
authoritative cache state remains replayable
same nonce replays
response hash is valid
delivery authority = verification cache
paid response is delivered
```

Expected specialized findings:

- `VERIFICATION_CACHE_SURVIVES_SETTLEMENT_FAILURE`;
- `VERIFICATION_USED_AS_DELIVERY_AUTHORITY`;
- `REPLAY_DELIVERY_AFTER_FAILED_SETTLEMENT`.

The generic core also emits `DELIVERED_BUT_NOT_SETTLED`. No duplicate settlement
or payer loss is claimed because the trace contains no successful settlement.

### Correct recovery — verified

`x402toll_failed_settlement_recovery.json`

```text
first credential verifies
first settlement fails
cache is revoked before reuse
same-credential replay is denied
fresh credential verifies and settles
one receipt and one response are delivered
terminal reconciliation completes
```

Expected verdict: `VERIFIED`.

### Verification without finality — unresolved

`x402_verified_finality_unknown.json`

Verification succeeds, but no independent finality observation exists and no
resource is delivered. Expected finding:

```text
SETTLEMENT_FINALITY_EVIDENCE_MISSING
UNRESOLVED
```

Additional unit regressions cover late settlement, late cache revocation,
settlement-before-replay, strict duplicate contracts, non-authoritative cache
claims, fresh authorization, and independent entitlement.

## Claim boundary

The public x402toll report establishes one live defect class and its regression
shape. This profile does not claim that x402 verification generally permits free
shopping, that all idempotency caches are unsafe, or that response provenance is
weak. It proves a narrower invariant:

```text
valid credential
+ successful verification
+ authentic response
!=
final paid entitlement
```

The stronger replay-delivery finding requires the same typed payment identity,
authoritative failed finality, a later delivery, and no matching settlement or
separate entitlement existing before that delivery.

## Commercial boundary

This profile is aimed at merchants, gateways, facilitators, and paid-agent
services whose delivery path spans verification, settlement, cache/idempotency,
and response generation. It complements policy engines and response-integrity
schemes by checking whether the resource was economically authorized at the
moment it was released.
