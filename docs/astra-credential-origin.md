# Astra payment-credential origin binding

This contract verifies the network-principal edge between an accepted payment
challenge, the resulting spend credential, and the origin that receives or
consumes that credential.

Public source:

- mppx PR #850 review and regression fix:
  https://github.com/wevm/mppx/pull/850#discussion_r3882290868

## State edge

```text
REQUEST / REDIRECT
  -> QUOTE/CHALLENGE origin
  -> MANDATE/AUTHORIZATION credential binding
  -> PAYMENT ATTEMPT credential recipient
  -> ACTUAL SETTLEMENT consumer origin
```

A redirect target and a redirecting origin are different network principals.
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

An authenticated intermediary can be admitted only through an authoritative
`authorized_credential_delegate_origin` event. An HTTP redirect, proxy hop, or
observed forwarding path is not sufficient delegation evidence.

## Origin comparison

Astra compares URL origins, not full resource URLs:

- scheme and host are case-normalized;
- IDNA hostnames are normalized;
- paths, queries, and fragments are ignored;
- default `http:80` and `https:443` ports are removed;
- non-default ports remain significant;
- IPv6 origins are serialized with brackets; and
- userinfo and malformed origins are rejected.

## Findings

| Code | Meaning |
|---|---|
| `CREDENTIAL_ORIGIN_EVIDENCE_MISSING` | Challenge and credential-binding principals cannot be compared safely. |
| `AUTHORIZATION_ORIGIN_DIVERGENCE` | The credential was bound to neither the challenge origin nor an authenticated delegate. |
| `CREDENTIAL_DISPATCH_ORIGIN_INVALID` | A reported credential recipient is not a valid URL origin. |
| `PAYMENT_CREDENTIAL_ORIGIN_DIVERGENCE` | A reusable credential was delivered to an origin outside the accepted challenge/delegation boundary. |
| `CROSS_ORIGIN_CREDENTIAL_REUSE` | One payment authorization was dispatched to multiple origins, creating a consumption race. |
| `SETTLEMENT_CONSUMER_ORIGIN_DIVERGENCE` | Authoritative evidence attributes credential consumption to an unauthorized origin. |

Origin exposure is kept separate from economic outcome:

- dispatch divergence proves the wrong principal received the credential;
- cross-origin reuse proves multiple principals received the same payment
  identity;
- neither alone proves that money moved; and
- confirmed settlement by the wrong principal requires authoritative consumer
  attribution.

## Red fixture

`mppx_cross_origin_credential_exposure.json` models the reviewed pre-fix path:

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

`mppx_challenge_origin_direct_retry.json` follows the regression contract:

```text
origin A -> redirect -> origin B
origin B issues challenge
credential retry goes directly to B
redirect traversal is not reused for the credential-bearing request
one settlement is attributed to B
receipt, resource delivery, and reconciliation agree
-> VERIFIED
```

## Claim boundary

The upstream review and regression test establish an unsafe delivery path and
its fix. They do not establish a historical credential theft, duplicate charge,
or wrong-origin settlement. Astra preserves those as stronger, evidence-gated
claims.
