# 120-second demo script

## 0:00-0:15 — The risk
"An AI agent can be correct when it plans an action and still be wrong when it executes it. AgentProof verifies the world again at the execution seam and refuses to claim success until reality agrees."

## 0:15-0:40 — Autonomous goal
Type a natural-language payment goal. Gemini 3.6 Flash selects the AgentProof payment tool. Show that the LLM proposes/chooses the action but does not own the final verdict.

## 0:40-1:05 — Stale authorization
Authorize invoice #7842 for $50,000, freeze the vendor between authorization and dispatch, then show `BLOCKED`, policy epoch 17 -> 18, ledger count 0, money moved $0.

## 1:05-1:30 — False-success trap
Run the provider mode that returns `ACCEPTED` without a ledger settlement. Show `UNVERIFIED` and `claimed_success: false`.

## 1:30-1:50 — Replay
Run the same execution twice. Show the first as `VERIFIED`, second as `DUPLICATE`, and the ledger still contains exactly one $50,000 payment.

## 1:50-2:00 — Close
"Don't trust what an AI agent says it did. Prove what actually happened. AgentProof makes autonomous execution inspectable, replay-safe, and outcome-grounded."
