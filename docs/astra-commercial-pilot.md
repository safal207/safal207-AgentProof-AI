# Astra Causal Payment Verification Sprint

## The offer

Astra is a focused verification sprint for agentic-payment systems. It does not
review whether a policy engine *allowed* a payment. It tests whether the full
post-decision economic chain remained coherent:

```text
PAYMENT ATTEMPT
  -> CLAIMED RESULT
  -> ACTUAL SETTLEMENT/FINALITY
  -> RECEIPT
  -> RESOURCE/OUTCOME DELIVERY
  -> RECONCILIATION
```

The core question is:

> Did one authorized business operation produce exactly the payment, delivery,
> receipt, and terminal ledger outcome that the system claims?

## What a client receives

A standard sprint covers one payment path and includes:

1. a protocol and system state graph;
2. three to five failure-injection probes at adjacent state transitions;
3. deterministic regression fixtures;
4. a machine-readable evidence report with a canonical SHA-256 trace digest;
5. an evidence table separating observed facts, code-derived implications, and
   unresolved assumptions;
6. an upstream-ready issue, regression test, or patch when the defect is
   localizable;
7. a short remediation and reconciliation contract.

Typical probes include:

- stale authorization accepted after the advertised challenge window;
- retry producing two economic settlements;
- failure claimed while the chain or PSP shows settlement;
- successful settlement without resource delivery;
- delivery before deferred capture;
- over-capture across partial or repeated captures;
- receipt or local ledger state disagreeing with stronger evidence;
- an asynchronous workflow remaining permanently nonterminal.

## Recommended entry pricing

These are proposed commercial packages, not market-price claims.

| Package | Scope | Proposed fixed price |
|---|---|---:|
| Evidence Triage | One trace, two probes, evidence/claim-boundary memo | USD 750 |
| Verification Sprint | One payment path, 3–5 probes, fixtures, report, regression test | USD 2,500 |
| CI Conformance Integration | Reusable adapter, CI gate, expanded protocol matrix | From USD 5,000 |

For the first lighthouse engagement, the cleanest offer is the USD 750 Evidence
Triage. Credit the full amount toward a Verification Sprint if the client
continues. Never promise a vulnerability; sell a reproducible answer and a
usable regression artifact.

## Acceptance criteria

A sprint is complete when:

- the observed payment path is represented as a state graph;
- every conclusion points to preserved evidence;
- all fixtures are reproducible without secret material;
- retries and idempotency are distinguished correctly;
- missing evidence is not overstated as proof of global absence;
- the client receives at least one concrete next action even when no defect is
  confirmed.

## Current public proof

Run:

```bash
python scripts/run_astra_fixtures.py
```

The initial suite demonstrates:

1. x402 `PAYMENT-RESPONSE` state conflicting with client-local ledger derivation;
2. an EIP-3009 authorization outliving the resource server's advertised window;
3. resource delivery occurring before deferred capture is confirmed.

The source incidents and protocol surfaces are linked from
[`astra-spider.md`](astra-spider.md).

## Initial target shortlist

Only public GitHub channels are listed. No private or inferred email address is
used.

### 1. RunOnProof / `@AureumOne`

- **Public evidence:**
  [x402 issue #3266](https://github.com/x402-foundation/x402/issues/3266)
  reports real Base-mainnet settlements that returned Bazaar `processing` but
  remained absent from discovery after repeated read-only checks.
- **Transition:** `ACTUAL SETTLEMENT/FINALITY -> CLAIMED ASYNC RESULT -> RECONCILIATION`.
- **Why first:** the operator already has paid-call evidence and explicitly
  avoids making another payment without justification.
- **Public channel:** the GitHub issue above.
- **Outreach angle:** offer a no-repeat-payment evidence normalization pass that
  binds the existing settlement, extension response, resource outcome, and
  subsequent discovery observations into one terminal/nonterminal trace.
- **Next probe:** ingest the existing transaction/receipt material privately or
  sanitized, then perform read-only reconciliation until a terminal catalog
  state or a bounded `RECONCILIATION_NOT_TERMINAL` finding is reached.

Suggested opening:

> You already have the evidence boundary needed for a useful no-repeat-payment
> fixture: one real settlement, a `processing` extension response, and repeated
> read-only absence checks. We are building Astra to normalize exactly this kind
> of post-payment chain without treating another charge as a diagnostic step.
> Would a sanitized fixture and terminal-state evidence contract be useful?

### 2. x402 Foundation / `@phdargen`

- **Public evidence:**
  [PR #3251](https://github.com/x402-foundation/x402/pull/3251) fixes clients
  copying untrusted batch-settlement `channelState` into local storage;
  `@phdargen` also authored the current auth-capture lifecycle revision.
- **Transition:** `CLAIMED RESULT -> CLIENT LEDGER/RECONCILIATION`, plus
  `RESOURCE DELIVERY -> DEFERRED CAPTURE`.
- **Public channel:** PR #3251 or a focused x402 discussion/issue.
- **Outreach angle:** Astra has converted the local-truth rule into a
  protocol-neutral fixture and can help define a cross-SDK conformance boundary.
- **Next probe:** run the same conflicting cumulative-state vector against
  pre-fix and current TypeScript, Go, and Python clients; preserve state before
  and after the callback.

Suggested opening:

> We converted the local-truth boundary from #3251 into a protocol-neutral
> causal fixture: `PAYMENT-RESPONSE` is evidence, not ledger authority. The same
> harness can cover TS, Go, and Python and then extend to deferred auth-capture.
> Which edge cases would you consider mandatory for a cross-SDK conformance set?

### 3. x402 Go SDK / `@Tehsapper`

- **Public evidence:**
  [PR #3282](https://github.com/x402-foundation/x402/pull/3282) changes Go
  EIP-3009 `validBefore` from a fixed one-hour lifetime to
  `maxTimeoutSeconds`.
- **Transition:** `QUOTE/CHALLENGE -> MANDATE/AUTHORIZATION`.
- **Public channel:** PR #3282.
- **Outreach angle:** turn the fix into a portable cross-SDK invariant rather
  than a one-language regression test.
- **Next probe:** issue one 30-second challenge, create Go/TS/Python payloads,
  compare `validBefore`, and attempt verify/settle after 45 seconds in a safe
  test environment.

Suggested opening:

> This fix exposes a useful cross-SDK invariant:
> `authorization.validBefore <= challenge expiry`. We now have a small Astra
> fixture for that boundary. Would a Go/TS/Python conformance vector, including
> a post-expiry verify/settle probe, be useful alongside the PR test?

### 4. Google AP2 / issue reporter `@giorgioroth`

- **Public evidence:**
  [AP2 issue #308](https://github.com/google-agentic-commerce/AP2/issues/308)
  records `used=true` and allocates an order before a PSP call, leaving that
  local state after a connection failure.
- **Transition:** `PAYMENT ATTEMPT/LOCAL ORDER STATE -> ACTUAL SETTLEMENT/FINALITY`.
- **Public channel:** AP2 issue #308.
- **Outreach angle:** formalize the distinction between credential consumed,
  settlement pending, settlement failed, and settlement confirmed without
  choosing the maintainer's ownership model for them.
- **Next probe:** PSP unavailable, then retry with the same and a fresh
  credential; compare token, order, receipt, transaction, and reconciliation
  states.

Suggested opening:

> We are implementing a protocol-neutral fixture for the exact boundary in
> #308: local token/order state must not become settlement truth. The fixture can
> remain neutral on whether AP2 samples or integrators own reconciliation. It
> would only expose the four observable states and their retry behavior. Would
> that be useful while the intended contract is being decided?

### 5. MPP / mppx / `@brendanjryan`

- **Public evidence:**
  [mppx PR #846](https://github.com/wevm/mppx/pull/846) separates missing,
  rejected, malformed, and internal payment failures and adds bounded
  verification retries.
- **Transition:** `CLAIMED FAILURE -> RETRY DECISION -> PAYMENT ATTEMPT -> SETTLEMENT`.
- **Public channel:** PR #846.
- **Outreach angle:** extend transport-level error correctness into an economic
  retry conformance set that counts credential creation, payment attempts, and
  actual settlements.
- **Next probe:** run missing credential, rejected credential, malformed
  metadata, and internal processor failure through raw JSON-RPC and MCP SDK
  transports; assert identical retry/no-retry behavior and at most one
  settlement.

Suggested opening:

> #846 gives clients the vocabulary needed for correct retry decisions. We are
> building an Astra fixture that checks the economic consequence as well: same
> error vector, same retry count, and at most one settlement across raw JSON-RPC
> and MCP SDK transports. Would that complement the protocol-mapping coverage?

## Strategic platform target

Amazon Bedrock AgentCore Payments is a valuable second-wave target. The public
sample in
[awslabs/agentcore-samples PR #1869](https://github.com/awslabs/agentcore-samples/pull/1869)
already demonstrates bounded payment sessions, merchant binding, delegated
signing, an x402 paid retry, and HTTP 200. The Astra angle is deliberately
post-policy:

```text
PROOF GENERATED != SETTLED != PAID RESPONSE DELIVERED != RECONCILED
```

A safe probe is to lose the paid HTTP response after payment processing, retry
with the same idempotency token, then compare that result with a retry using a
new token. This target becomes stronger after the three public fixtures and one
real paid-evidence case are visible.

## Outreach discipline

- Start with a technical question, not a sales pitch.
- Link one fixture that matches the target's own public evidence.
- Do not claim loss of funds unless finality evidence proves it.
- Do not publish transaction details, secrets, signatures, or payer identity.
- Do not ask a target to make a second payment merely to reproduce an
  observability or reconciliation issue.
- Keep commercial terms out of an upstream issue unless the project explicitly
  invites paid support; move business discussion only to a publicly confirmed
  company channel supplied by the target.
