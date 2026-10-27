# S3 development diagnostic v5 — c004 with interaction prompt v6

Status: offline frozen candidate, not authorized for provider calls.

## Scope

- One development trajectory only: public synthetic case c004, incremental method, R=1.
- Same debugged scenario family. This is not an independent or generalization sample and cannot be combined across rounds for method ranking.
- The only method change from the prior c004 run is the versioned interaction prompt v6, which explicitly requires user-source authorization evidence for every server/API grant. The state kernel, evidence bundle, model configuration and output contract remain versioned and hashed.
- No baseline rerun. The v4 c004 attempt remains a preserved negative result; no response is repaired or reused as a live model result.

## Execution and safety

- Provider: DeepSeek `deepseek-flash`, temperature 0, maximum 4096 output tokens; single concurrency, 90 seconds per request, no retries.
- Maximum 8 provider attempts for the whole trajectory: up to 5 event extractions, 2 model-selected lookup continuations and 1 terminal review. The model may choose lookup only for visible earlier event IDs. No lookup is forced.
- Each request has a 100,000-byte ceiling. The local reservation uses the conservative peak quote for 100,000 input bytes and the frozen output allowance; the proposed round cap is 3.00 CNY. Provider billing can differ; the local reserve is not a provider hard cap.
- Stop on the first provider, schema, usage, transition, replay-integrity or budget failure. No retry, resume, continuation, or rerun under the same plan.
- Validate plan, runner/source hashes, fresh official price and exact-scope authorization before reading credentials. The only real command is one-use and writes a unique run directory. Offline prepare/verify/simulation never read credentials or private labels.

## Data boundary and outputs

If separately authorized, send only the frozen plan's public synthetic c004 history, public candidate code and contract/evidence, v6/v4 prompts, and earlier raw event text only if requested through the lookup mechanism. Do not send evaluator labels or private reference state.

Save every request, raw response or provider failure, usage, local reserve, timing, state/context transition, lookup record, stop point, source hash and replay result. The report preserves one planned denominator, errors and missing terminal output. A real lookup claim requires a model-selected request, correctly returned trusted source text, accepted continuation and valid terminal output in this same trajectory. Scripted controls are only wiring/failure evidence. Even a completed real trajectory is author-review development evidence, not independent review, comparative performance, or full S3 research acceptance.

## Frozen price proposal

Official DeepSeek pricing was checked at `2026-10-26T14:34:37Z`: peak Flash cache-miss input USD 0.30 / million tokens and output USD 1.20 / million tokens. At the repository's conservative 10 CNY/USD accounting proxy, 8 maximum reservations at 100,000 bytes each total 2.989824 CNY. Proposed local cap: 3.00 CNY. The conversion is a budget proxy, not an exchange-rate quote. Recheck immediately before any future authorization/run; provider bill remains unknown.

## Proposed paid-run authorization

Plan SHA-256 is recorded in `reports/local/s3-development-v5-preflight-20261026/plan.json` and must be restated in any new approval. Require both explicit approval of the 3.00 CNY cap and explicit consent to the exact outbound-data scope above. Prior c004/v4 consent was tied to its consumed plan and is not reused. Without both approvals, do not read the provider key or send any request.
