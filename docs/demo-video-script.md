# AgentProof — 4-minute Devpost demo video script

**Target duration:** 3:50 (acceptable range: 3:30–4:00)
**Live demo URL:** `https://agentproof-ssejdi5rra-uc.a.run.app`
**Demo data:** simulated $50,000 invoice — no real funds move.

## Recording setup — do this before recording

- Open the live AgentProof URL in a clean browser profile at a readable zoom level.
- Open `docs/architecture.png` in a second window or tab, full screen.
- Keep the GitHub repository, the green GitHub Actions result, and `docs/evidence/agentproof-live-verification-2026-08-20.json` in separate prepared tabs.
- For the two Gemini moments, use the browser Network response (or a safe local HTTP client) to reveal the JSON fields `tool_call.name`, `verdict_authority`, and the receipt. Do not show tokens, terminal history, Cloud Console account menus, ADC files, service-account details, billing, or project identifiers.
- Record at 1080p. Use large browser text and pause about one second after each verdict appears.
- Before the real take, run the normal and stale Gemini goals once to warm the service; then reset the page and record a fresh take.

## 0:00–0:25 — Problem

**Screen:** AgentProof home page. Keep invoice #7842, ACME Corp, and simulated $50,000 visible.

**Say:**

> Autonomous agents can receive valid permission, but state may change before execution. An API accepting a request also does not prove the intended outcome happened.
>
> AgentProof is a verification runtime for that gap: it checks whether an action is still authorized at the execution seam, and whether the expected state transition actually occurred.

## 0:25–0:45 — Architecture

**Screen:** `docs/architecture.png`, zoomed so all four verdicts are visible.

**Point out:**

- Purple decision layer: Gemini live route and the separate Google ADK integration entrypoint.
- Blue deterministic AgentProof boundary: authorization, revalidation, idempotent dispatch, and outcome verification.
- The four independently emitted safety verdicts leading to evidence and audit.

**Say exactly:**

> Gemini decides what to do. AgentProof decides what actually happened.

## 0:45–1:20 — Normal: natural language → Gemini → receipt

**Screen:** Return to the live Cloud Run page. In **Autonomous Gemini goal**, enter:

```text
Pay the already approved ACME invoice through the normal workflow.
```

Click **Run with Gemini**. Show the live verdict, evidence receipt, and the safe response fields from the Network response:

```text
tool_call.name: run_agentproof_payment
verdict_authority: AgentProof deterministic receipt
receipt.status: VERIFIED
```

**Say:**

> This is a real natural-language request to Gemini 3.6 Flash. Gemini makes one constrained tool call: `run_agentproof_payment`.
>
> The action then enters AgentProof's deterministic core. The simulated $50,000 ledger transition is observed, so the receipt is `VERIFIED`.

## 1:20–2:05 — Stale authorization: hero moment

**Screen:** Replace the goal with:

```text
The ACME vendor was frozen after authorization and before dispatch. Attempt the payment and report the verified safety outcome.
```

Click **Run with Gemini**. Hold on `BLOCKED`, the receipt event timeline, and **Money moved: $0**. If helpful, expose the same safe tool-call fields in the Network response.

**Say exactly:**

> The agent had permission when it planned the payment.
> It did not have permission when execution began.

Then add:

> AgentProof re-checks freshness and action binding at dispatch, blocks the stale grant, and emits a machine-readable `BLOCKED` receipt. No simulated money moves.

## 2:05–2:40 — False success

**Screen:** Click **False success trap**. Keep the `UNVERIFIED` verdict and receipt event stating that settlement is absent visible.

**Say exactly:**

> Accepted is not settled. Dispatch is not outcome.

Then add:

> The provider accepts the request, but the expected ledger state is absent. AgentProof refuses to turn an API acknowledgement into a success claim.

## 2:40–3:05 — Replay protection

**Screen:** Click **Replay attack**. Show `DUPLICATE`, the receipt, and the ledger payment count remaining at one.

**Say:**

> Retrying the same execution key does not create a second payment. The first action is the only simulated settlement; the retry becomes `DUPLICATE`.
>
> The same atomic claim protects against competing concurrent retries, so multiple attempts still cannot create a second execution.

## 3:05–3:30 — Proof and production readiness

**Screen sequence:**

1. Briefly show the public `.run.app` URL in the browser address bar.
2. Show the GitHub repository and the green GitHub Actions checks.
3. Show the test result `10 passed` from a pre-recorded, credential-free test run, without shell profiles or paths.
4. Show the top of `docs/evidence/agentproof-live-verification-2026-08-20.json`: Cloud Run PASS, the four runtime verdicts, live Gemini PASS, and `secrets_exposed: false`.

**Say:**

> This is deployed on Google Cloud Run and backed by repeatable tests, live runtime probes, and a safe verification record. The model can propose a tool call, but it cannot rewrite AgentProof's receipt.

## 3:30–3:55 — Closing

**Screen:** Return to the main verdict panel or the architecture diagram. End on the AgentProof headline.

**Say exactly:**

> AI agents should be free to act autonomously.
> But autonomy without verification is just trust.
>
> AgentProof turns agent actions into evidence.
>
> Don't trust what an AI agent says it did.
> Prove what actually happened.

## Final recording checklist

- [ ] Architecture PNG is readable at full-screen 1080p.
- [ ] One live Gemini normal request visibly produces `run_agentproof_payment` → `VERIFIED`.
- [ ] One live Gemini stale request visibly produces `run_agentproof_payment` → `BLOCKED`, `$0 moved`.
- [ ] False success shows `UNVERIFIED`; replay shows `DUPLICATE` and one payment only.
- [ ] All shown payments are labelled simulated.
- [ ] Cloud Run URL, GitHub repository, CI PASS, tests, and safe verification JSON are visible.
- [ ] No token, account, project, billing, ADC, or service-account information is on screen.
- [ ] Final exported video is 3:30–4:00, 1080p, with readable captions or narration.
