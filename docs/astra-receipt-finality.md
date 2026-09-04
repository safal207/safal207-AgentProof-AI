# Astra independent receipt-to-rail finality binding

A cryptographically valid receipt proves who signed an exact claim. It does not
prove that the claim is economically true. This profile verifies whether a
signed payment-success receipt is independently supported by payment-rail
finality and confirmation evidence for the same operation and typed payment
identity.

## Public evidence

`google-agentic-commerce/AP2#327` documents that the Python receipt helper and
MPP sample can issue and sign `PaymentReceipt(status="Success")` after mandate
verification while generating `psp_confirmation_id` and
`network_confirmation_id` locally, without a payment-rail lookup.

Open AP2 PR `#335` proposes an additive `rail_confirmation_verified` flag and
explicitly keeps rail verification outside the generic SDK helper. The helper
sets the flag when callers supply both confirmation IDs. That is useful
disclosure, but the IDs and flag remain issuer claims until an independent
resolver checks them against the PSP/network.

Public channels:

- `google-agentic-commerce/AP2#327`, reporter `@mh-yu`;
- `google-agentic-commerce/AP2#335`, author `@babyblueviper1`;
- no personal email is asserted by this profile.

## State edge

```text
MANDATE/AUTHORIZATION
  -> PAYMENT ATTEMPT
  -> ACTUAL SETTLEMENT/FINALITY
  -> RECEIPT (signed status and confirmation claims)
  -> RESOURCE/OUTCOME DELIVERY
  -> RECONCILIATION
```

## Core invariant

> Receipt integrity, receipt status, confirmation-ID presence, and an
> issuer-provided verification flag are separate claims. A Success receipt is
> independently verified only when authoritative settled finality and matching
> PSP/network confirmation evidence for the same typed payment identity and
> operation existed no later than receipt issuance.

A valid signature can coexist with a false, premature, or unverifiable payment
claim. Conversely, a receipt that leaves `rail_confirmation_verified` false can
still be independently verified later when Astra receives matching rail
evidence.

## Opt-in contract

```json
{
  "stage": "POLICY DECISION",
  "key": "requires_independent_receipt_finality_binding",
  "value": {
    "required": true,
    "success_requires_settled_finality": true,
    "verified_flag_is_claim_only": true
  },
  "operation_id": "operation-1"
}
```

All three booleans are required. The contract is intentionally strict: an
integration may not redefine an issuer flag as authoritative payment truth.
A global contract may be combined with an operation-specific contract; invalid
operation-specific declarations fail closed for that operation.

## Receipt evidence

### Raw receipt status

```json
{
  "stage": "RECEIPT",
  "key": "receipt_status",
  "value": "Success",
  "operation_id": "operation-1",
  "payment_id": "payment-1"
}
```

`receipt_status` preserves raw receipt vocabulary such as AP2 `Success` and
avoids requiring protocol adapters to rewrite signed content. The verifier also
accepts `payment_status` at `RECEIPT` for adapters that already normalize their
receipt status.

Success aliases include `Success`, `settled`, `paid`, `confirmed`, `captured`,
and `complete`. Failure aliases include `Error`, `failed`, `rejected`,
`reverted`, `expired`, and `not_settled`.

### Receipt integrity

```json
{
  "stage": "RECEIPT",
  "key": "receipt_integrity_status",
  "value": "verified",
  "authoritative": true,
  "operation_id": "operation-1",
  "payment_id": "payment-1"
}
```

This evidence comes from a signature/JWS verifier trusted by the integration.
The receipt's own assertion that it is signed is not sufficient.

### Receipt confirmation claim

```json
{
  "stage": "RECEIPT",
  "key": "receipt_rail_confirmation",
  "value": {
    "psp_confirmation_id": "psp-123",
    "network_confirmation_id": "network-456",
    "rail_confirmation_verified": true
  },
  "operation_id": "operation-1",
  "payment_id": "payment-1"
}
```

The boolean is preserved exactly as an issuer claim. `true` does not set
`authoritative=true` and does not promote the receipt to rail truth.

## Independent rail evidence

### Terminal payment status

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "payment_status",
  "value": "settled",
  "authoritative": true,
  "operation_id": "operation-1",
  "payment_id": "payment-1",
  "observed_at": "2026-08-17T12:59:50Z"
}
```

### PSP/network confirmation record

```json
{
  "stage": "ACTUAL SETTLEMENT/FINALITY",
  "key": "rail_confirmation",
  "value": {
    "psp_confirmation_id": "psp-123",
    "network_confirmation_id": "network-456"
  },
  "authoritative": true,
  "operation_id": "operation-1",
  "payment_id": "payment-1",
  "observed_at": "2026-08-17T12:59:51Z"
}
```

Both confirmation IDs are compared exactly and separately. Their mere presence,
UUID shape, or equality to a locally generated payment ID proves nothing about
rail verification.

## Identity and operation binding

Receipt and rail evidence must share at least one typed identifier:

- `authorization_id`; or
- `payment_id`.

The two fields are separate namespaces. Equal text in `authorization_id` and
`payment_id` is not a cross-field match. The `operation_id` must also match.
Evidence for another payment or another operation cannot make the receipt true.

Multiple receipt records for the same typed identity must agree. Multiple
authoritative rail-confirmation records for the same typed identity must also
agree; a matching record does not erase a conflicting duplicate.

## Temporal rule

Receipt truth is evaluated **at receipt issuance**, not with hindsight.
`observed_at` is used when both events provide an absolute time; otherwise trace
order is used.

```text
receipt issued with rail_confirmation_verified=true
-> independent settlement appears later
```

The later settlement may prove eventual payment, but it does not prove that the
issuer independently checked finality before signing the earlier receipt.
Astra therefore keeps the receipt claim unresolved and emits
`RECEIPT_VERIFICATION_TIMING_UNPROVEN`.

## Findings

| Code | Meaning |
|---|---|
| `RECEIPT_FINALITY_BINDING_CONTRACT_INVALID` | The integration does not explicitly require settled finality or treat the issuer flag as claim-only. |
| `RECEIPT_STATUS_EVIDENCE_MISSING` | The contract is active but no receipt status claim is supplied. |
| `RECEIPT_STATUS_EVIDENCE_INVALID` | The receipt status is not a recognized success or failure state. |
| `RECEIPT_EVIDENCE_CONFLICT` | Receipt records for one typed identity disagree about status, confirmation IDs, or flag. |
| `RECEIPT_INTEGRITY_EVIDENCE_MISSING` | No authoritative successful signature/integrity result is supplied. |
| `RECEIPT_INTEGRITY_FAILED` | Independent signature/integrity verification failed. |
| `RECEIPT_INTEGRITY_EVIDENCE_CONFLICT` | Authoritative integrity checks disagree. |
| `RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY` | No matching settled finality existed at receipt issuance. |
| `RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING` | A valid matching PSP/network confirmation record did not exist at issuance. |
| `RECEIPT_RAIL_VERIFICATION_CLAIM_UNSUPPORTED` | The issuer set `rail_confirmation_verified=true`, but complete matching independent evidence is absent. |
| `RECEIPT_CONFIRMATION_ID_MISMATCH` | Receipt confirmation IDs differ from authoritative rail IDs. |
| `RECEIPT_SETTLEMENT_IDENTITY_UNRESOLVED` | Receipt and rail evidence expose no common typed payment identity. |
| `RECEIPT_SETTLEMENT_IDENTITY_DIVERGENCE` | Rail evidence conflicts with the receipt payment identity or operation. |
| `RECEIPT_RAIL_EVIDENCE_CONFLICT` | Authoritative rail-confirmation records for the same payment disagree. |
| `RECEIPT_SUCCESS_FINALITY_CONFLICT` | A Success receipt conflicts with authoritative non-settlement at issuance. |
| `RECEIPT_VERIFICATION_TIMING_UNPROVEN` | Matching independent evidence appears only after receipt issuance. |

Medium findings keep the receipt `UNRESOLVED`. High or critical findings produce
`DIVERGED`.

## Fixture set

### AP2 helper/source boundary — unresolved

`ap2_signed_success_generated_ids_no_rail.json`

A valid signed Success receipt uses generated confirmation IDs and has no rail
lookup. Expected:

```text
RECEIPT_SUCCESS_WITHOUT_VERIFIED_FINALITY
RECEIPT_RAIL_CONFIRMATION_EVIDENCE_MISSING
UNRESOLVED
```

### Caller-supplied verified flag — unresolved

`ap2_verified_flag_without_rail_evidence.json`

The receipt sets `rail_confirmation_verified=true`, but no independent evidence
exists. The flag is reported as unsupported rather than accepted as authority.

### Independent rail match — verified

`ap2_independent_rail_confirmation_reconciled.json`

Settlement and matching PSP/network confirmation evidence exist before receipt
issuance. Receipt integrity, delivery, and terminal reconciliation agree.
Expected verdict: `VERIFIED`.

### Success versus failed rail — divergent

`ap2_success_conflicts_failed_finality.json`

Authoritative non-settlement exists before a valid signed Success receipt.
Expected: critical `RECEIPT_SUCCESS_FINALITY_CONFLICT`.

### Confirmation-ID mismatch — divergent

`ap2_confirmation_id_mismatch.json`

The payment settled, but the signed receipt's PSP/network IDs do not match the
rail record. Delivery may have occurred, yet receipt-to-settlement binding is
wrong.

### Late evidence — unresolved

`ap2_late_finality_after_receipt.json`

Matching settlement and confirmation records appear only after a receipt that
claimed prior verification. The eventual payment is not allowed to rewrite the
receipt's earlier verification claim.

## Claim boundary

The AP2 source demonstrates that a helper/sample can self-issue a signed Success
receipt without rail evidence. It does not prove that production merchants
release goods or entitlements from those receipts. PR #335 improves disclosure
and intentionally leaves business/PSP-specific verification outside the helper.

Astra does not prescribe one PSP implementation. It classifies each receipt as:

```text
authentic and independently verified
authentic but unresolved
authentic but contradicted by the rail
not independently authenticated
```

## Commercial boundary

Signed receipts are likely to become entitlement, accounting, dispute, and audit
inputs across agent commerce. This module prevents downstream systems from
converting an authentic issuer claim into economic truth before the referenced
rail facts are independently resolved.
