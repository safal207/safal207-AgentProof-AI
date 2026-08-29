# Astra adapter profile: Obolus/x402 ambiguous-settlement retry

## Purpose

This profile composes Astra's merged indeterminate-settlement retry contract
with downstream economic evidence from the public Obolus Phase A boundary.
The generic contract already detects a fresh authorization created before the
previous payment is resolved. This profile adds the stronger consequence case:
when two independently authoritative settlements are later observed for one
business operation, the finding escalates from an unsafe retry contract to
confirmed duplicate settlement and over-capture within the supplied trace.

The profile does **not** claim that a live Obolus deployment has double-charged a
payer. The public record establishes a reproducible design boundary:

- a settle response can be lost after a facilitator may have submitted payment;
- the gateway cannot infer definitive success or failure from transport loss;
- shipped reference clients create a fresh EIP-3009 nonce on a new top-level
  application retry;
- nonce replay protection therefore does not merge the two authorizations.

The red fixture's two authoritative settlements are a deterministic adversarial
consequence fixture, not reported live Obolus evidence.

Public source:

- `https://github.com/geekinasuit/obolus/issues/17`

Public owner/channel:

- GitHub user `@cgruber`, through the issue above. No email is asserted by this
  profile.

## State mapping

```text
REQUEST (one paid inference)
  -> QUOTE/CHALLENGE (expected settlement count = 1)
  -> MANDATE/AUTHORIZATION (EIP-3009 nonce A)
  -> PAYMENT ATTEMPT
  -> CLAIMED RESULT (timeout/failure surface)
  -> [unsafe retry: fresh nonce B]
  -> PAYMENT ATTEMPT
  -> ACTUAL SETTLEMENT/FINALITY (tx A and tx B)
  -> RECEIPT
  -> RESOURCE/OUTCOME DELIVERY (one response)
  -> RECONCILIATION
```

The causal identity is `operation_id`, not the nonce. An EIP-3009 nonce protects
one authorization against replay; it does not prove that a second nonce belongs
to a new business obligation.

## Red fixture

`fixtures/astra/obolus_x402_new_nonce_double_settlement.json`

One logical operation receives two authorizations and two authoritative
settlements. The authorized ceiling is the price of one inference, while the
observed settlement total is twice that amount.

Expected Astra findings:

- `FRESH_AUTHORIZATION_AFTER_INDETERMINATE_SETTLEMENT`
- `CLAIMED_FAILED_BUT_SETTLED`
- `RETRY_DUPLICATE_PAYMENT`
- `OVER_CAPTURE`

## Green fixture

`fixtures/astra/obolus_x402_same_authorization_reconciled.json`

The transport outcome is marked unknown rather than failed. Recovery preserves
`operation_id`, `authorization_id`, and `payload_hash`, explicitly declares the
same authorization economically idempotent, resolves the original payment
before any fresh authorization is created, then produces exactly one settlement,
one receipt, one delivery, and terminal reconciliation.

Expected Astra verdict: `VERIFIED`.

## Conformance rule

For an ordinary single-payment obligation:

```text
new authorization after an ambiguous result
  requires either
    independent proof that the previous authorization did not settle
  or
    a new operation_id representing a genuinely new business obligation
```

Keeping the same request body is insufficient: two users may buy the same
resource, and one user may intentionally buy it twice. The binding must be to a
business operation plus authoritative settlement evidence.

## Next live probe

Use Base Sepolia and a third-party facilitator:

1. Submit one EIP-3009 authorization.
2. Drop the facilitator/gateway response after submission but before the client
   receives it.
3. Independently resolve nonce A on-chain.
4. Run an ordinary application-level retry and record whether the client creates
   nonce B.
5. Assert one of two safe outcomes:
   - nonce A settled, so no second payment is authorized; or
   - nonce A is independently proven unused/expired before nonce B is signed.
6. Correlate transaction evidence, receipt, resource delivery, and terminal
   reconciliation under one sanitized `operation_id`.

Secrets, signatures, unrestricted wallet access, payer identity, and unnecessary
transaction details must not enter the fixture.

## Outreach angle

The useful message is not "Astra found an Obolus bug." It is:

> Your public Phase A record identifies the precise boundary where nonce-level
> replay protection stops protecting the business operation. Astra turns that
> boundary into a portable red/green conformance fixture tying authorization,
> finality, delivery, and reconciliation together.
