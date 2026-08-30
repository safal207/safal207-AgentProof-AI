# Astra payment-credential origin binding

This contract verifies the network-principal edge between an accepted payment
challenge, the reusable spend credential derived from it, and every origin that
receives or consumes that credential.

Public source:

- `wevm/mppx#850`, including the cross-origin redirect review and regression
  test that sends the credential-bearing retry directly to the challenge
  resource with redirect traversal disabled.

The upstream PR remains open at the time of this profile. The evidence supports
an unsafe pre-fix delivery path and the regression shape; it does not establish
historical credential theft or a completed wrong-origin settlement.

## State edge

```text
REQUEST / REDIRECT
  -> QUOTE/CHALLENGE origin
  -> MANDATE/AUTHORIZATION credential binding
  -> PAYMENT ATTEMPT credential recipient
  -> ACTUAL SETTLEMENT consumer origin
```

A redirect target and a redirecting origin are distinct network principals.
Following a redirect does not authorize the earlier origin to receive a
reusable payment credential intended for the challenge issuer.

## Opt-in evidence contract

Adapters declare:

```json
{
  "stage": "QUOTE/CHALLENGE",
  "key": "requires_credential_origin_binding",
  "value": true
}
```

They then supply:

- `challenge_origin` at `QUOTE/CHALLENGE`;
- `credential_bound_origin` at `MANDATE/AUTHORIZATION`;
- one `credential_dispatch_origin` event for every observed recipient at
  `PAYMENT ATTEMPT`; and
- optionally an authoritative `credential_consumer_origin` at
  `ACTUAL SETTLEMENT/FINALITY`.

Every dispatch should expose an `authorization_id` or `payment_id`. Without
that identity, Astra can identify a recipient but cannot exclude reuse of the
same credential across recipients. The dispatch must share at least one typed
identifier with the bound credential, and no shared typed identifier may
conflict. The two fields are separate identity namespaces: equal text in
`authorization_id` and `payment_id` does not create a match. The same typed
identity rule applies to authoritative settlement-consumer evidence. A scoped
delegate must match the same declared field on the target event.

An intermediary is admitted only through an authoritative
`authorized_credential_delegate_origin` event. Delegate evidence may be global
to one operation or scoped to one `authorization_id` / `payment_id`. An HTTP
redirect, proxy hop, DNS relation, or observed forwarding path is not delegation
evidence.

## Origin comparison

Astra compares HTTP(S) origins, not full resource URLs:

- scheme and host are case-normalized;
- IDNA hostnames are normalized;
- paths, queries, and fragments are ignored;
- default `http:80` and `https:443` ports are removed;
- non-default ports remain significant;
- IPv6 origins are serialized with brackets;
- userinfo is rejected; and
- malformed or non-HTTP(S) origins are rejected.

## Findings

| Code | Meaning |
|---|---|
| `CREDENTIAL_ORIGIN_EVIDENCE_MISSING` | Challenge and credential-binding principals cannot be compared safely. |
| `AUTHORIZATION_ORIGIN_DIVERGENCE` | The credential is bound to neither the challenge origin nor an authenticated delegate for that payment identity. |
| `CREDENTIAL_DISPATCH_EVIDENCE_MISSING` | Origin verification is required, but no credential recipient appears in the trace. |
| `CREDENTIAL_DISPATCH_ORIGIN_INVALID` | A reported credential recipient is not a valid HTTP(S) origin. |
| `CREDENTIAL_IDENTITY_EVIDENCE_MISSING` | A dispatch omits both authorization and payment identity. |
| `CREDENTIAL_IDENTITY_BINDING_UNRESOLVED` | The bound credential and dispatch expose no common typed identifier. |
| `CREDENTIAL_IDENTITY_DIVERGENCE` | A typed dispatch identifier conflicts with the credential binding. |
| `PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE` | A reusable credential was delivered outside the accepted challenge/delegation boundary. |
| `CROSS_ORIGIN_CREDENTIAL_REUSE` | One payment identity was dispatched to multiple origins, creating a consumption race. |
| `SETTLEMENT_CREDENTIAL_IDENTITY_UNRESOLVED` | Consumer-origin evidence cannot be tied to the accepted credential through a common typed identifier. |
| `SETTLEMENT_CREDENTIAL_IDENTITY_DIVERGENCE` | Authoritative consumer-origin evidence refers to a conflicting credential identity. |
| `SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE` | Authoritative evidence attributes credential consumption to an unauthorized origin. |

Origin exposure is kept separate from economic outcome:

- identity divergence proves the dispatched credential is not the credential
  represented by the binding evidence, even if the network origin is correct;
- dispatch divergence proves the wrong principal received the credential;
- cross-origin reuse proves multiple principals received the same typed payment
  identity;
- settlement-consumer identity divergence proves that a correct network origin
  cannot be used to attribute consumption of a different credential;
- none of the dispatch findings alone proves that money moved; and
- confirmed settlement by the wrong principal requires authoritative consumer
  attribution.

## Red fixture

`fixtures/astra_origin/mppx_cross_origin_credential_exposure.json` models the
reviewed pre-fix path:

```text
origin A -> redirect -> origin B
origin B issues challenge
credential is bound to B
same authorization is dispatched to A and B
-> PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE
-> CROSS_ORIGIN_CREDENTIAL_REUSE
```

It contains no settlement event and therefore makes no claim that either origin
consumed the authorization.

## Green fixture

`fixtures/astra_origin/mppx_challenge_origin_direct_retry.json` follows the
regression contract:

```text
origin A -> redirect -> origin B
origin B issues challenge
credential-bearing retry goes directly to B
redirect traversal is disabled for that retry
one settlement for the same typed credential is attributed to B
receipt, resource delivery, and reconciliation agree
-> VERIFIED
```

## Claim boundary

This profile proves only what the supplied events support. An application log
showing a header at origin A is dispatch evidence, not settlement evidence. A
chain transaction proves value movement, but wrong-origin or wrong-credential
economic consumption requires a trustworthy binding between the payment
identity and the consuming origin.

Public working channel: `wevm/mppx#850`, owned by `@brendanjryan`. No email is
asserted by this profile.
