# S3 development diagnostic v8 — c004 with interaction prompt v8 and final review prompt v2

Status: offline frozen candidate only; `draft_unapproved`; no provider requests authorized.

## Scope

- One development trajectory: public synthetic c004, incremental method, R=1. This is the same debugged family, not an independent sample, generalization result, or method comparison.
- Uses interaction prompt v8 and `interaction_quote_v4`. The contract narrows candidate conditions to `candidate_sha256`, a lowercase 64-character digest, and artifact IDs from the public registry. The model prompt distinguishes history event IDs from artifact IDs; separates active constraints from a grant's exact-scope exception references; and requires historical conditions to remain unknown until requested history is returned and checked. No old response is rewritten.
- A cloned versioned state trajectory v4 uses the strict interaction parser throughout request creation and replay. The v5 actual c004 failure remains unchanged and is replayed only as an offline negative control.
- No baseline run. No private reference labels or state enter request construction, the provider interface, or simulations.

## Execution and safety

- Provider proposal: DeepSeek `deepseek-flash`, temperature 0, maximum 4096 output tokens; single concurrency, 90 seconds per request, no retries.
- At most 8 attempts for the one trajectory: up to five extractions, two model-selected history lookup continuations and one terminal review. Lookup is never forced.
- Per-request ceiling 100,000 wire bytes; local cap proposal 3.00 CNY. Worst-case reserve is 2.989824 CNY using a conservative 10 CNY/USD accounting proxy and peak rates. This is a local reservation estimate, not a provider hard bill cap.
- Stop at the first provider, schema, usage, state, replay-integrity, or budget failure. No retry, resume, continuation, or rerun under this plan.
- Validate exact plan, source hashes, fresh price record and exact-scope user approval before reading the API key. The offline prepare/verify/simulation paths do not read credentials or private labels.

## Data boundary

Only if separately approved: send the frozen public synthetic c004 history, public candidate code, contract/public evidence, interaction v8, final-review v2 and related prompts. Earlier raw event text can leave the workspace only after a model-selected lookup request and only for the requested IDs. Do not send private reference labels or reference state. Authorization for the consumed v5 plan does not carry over.

## Offline evidence

Six saved simulations exercise a complete mock lookup path, no lookup, the old v4 rejection, the preserved v5 invalid lookup continuation, a first provider error, and invalid JSON. All have zero real calls, null performance metrics and `s3_development_exit=not_satisfied`. The mock lookup path is `offline_transport_only`; no-lookup is `not_exercised`. The preserved v5 continuation remains a negative control under the strict parser as `invalid_output` after six simulated attempts; its original raw response and failure record remain unchanged. Ten targeted tests and saved replay/report checks are recorded in [the offline review](../docs/s3-development-v8-offline-review.md).

## Pricing record and proposed authorization

Official [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) was rechecked at `2026-10-26T15:35:16Z`: peak Flash cache-miss input is USD 0.30 per million tokens and peak output is USD 1.20 per million. This v8 preflight uses an 8-attempt / 3.00 CNY local cap and 2.989824 CNY worst-case reserve, calculated conservatively from peak rates. Price was rechecked at the timestamp above. This remains a local reservation estimate, not a provider hard bill cap; recheck if execution is delayed or pricing changes, and immediately before a real run. Provider actual billing remains unknown and may differ from the local estimate.

The exact candidate plan, request preview and hashes are under `reports/local/s3-development-v8-preflight-final-20261026/`; see [the v8 offline review](../docs/s3-development-v8-offline-review.md). A future real diagnostic requires new explicit approval of that exact plan hash, the 3.00 CNY limit, and the outbound data boundary above. This approval would cover one c004 diagnostic only; no rerun, other cases, method comparison, or full S3 acceptance.

Even a completed real trajectory would be one author-reviewed development observation. Independent review, complete S3 research acceptance, and S4 method comparison remain separate unfinished items.
