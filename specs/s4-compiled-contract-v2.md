# S4 compact contract and explicit compiler v2

2026-11-04. Offline successor to v5 SHA `cb8e107401102f00cba2b67623d9286b492ea062a631b0db76535b6d2c8665bd`. Its 253 source bindings, raw responses, failures, scores and consumed approval remain unchanged. This revision has no live launcher, key reader or approval intake. It is not v6 paid authorization or a formal experimental freeze.

## One source of each declaration

Final constraint items are exactly `{item_id,kind:"constraint",subjects,predicate,lifecycle,interpretation,source:{event_id,quote}}`. The typed predicate replaces a duplicated free-text predicate plus separate `api_constraints` entry. Authorizations keep the existing quote-derived shape including operations, conditions and sources. The six-field checklist remains required for full_checklist; all three finals use the same constraint compiler.

Incremental constraint operations retain current-event `source_quote`, subject, ID and lifecycle operation fields. Value-semantics predicate is `{kind:"value_semantics",expected:{source_text,explanation},qualifiers:{meaning_quote}}`. The one source must explicitly contain API endpoint/version, field and unconditional obligation, using the prior bounded source checks. Meaning quote must be unique in the same trusted user event and contained by that anchor; source_text must occur uniquely including overlapping occurrences. Advisory explanation is retained but excluded from normative identity. No paragraph joining, nearest-path inference, cross-item, cross-event or cross-version scope completion is performed. Choosing correct source and interpreting language remain model tasks, not a general semantic proof.

## Published compiler, not output repair

The program derives only redundant fields: scoped meaning source/event from the declared source; constraint source_refs from source plus meaning quote (deduplicated if identical); an internal typed API table; canonical normative description; empty constraint conditions for these unconditional predicates; and full operation coverage from the unchanged common map. FieldExists covers remove/replace; ValueSemantics covers semantic changes; ResponseShape covers remove/replace/semantic changes plus add_alias iff exact_keys=true. These are constraint scope, never only the candidate's operation. Conditional permissions stay in grants and use the old condition kernel. Unsupported conditional constraints still abstain.

Model-supplied compiler fields and old final layouts are rejected. Old output bytes are never normalized into the new contract or rescored. Fresh authored controls exercise the new wire format, while original v5 failures replay under their own version. Grants, exact values, scope, lifecycle, atomic rollback and exceptions use frozen kernels. Full history, common evidence/tool permissions, complete checklist, serial execution, no retry and complete cost/denominator accounting remain.

## Persistence and validation order

`wire bytes persisted -> strict operation-discriminated schema -> declared compiler -> exact trusted source checks -> legacy atomic kernel/final verifier -> checkpoint -> independent R4 reconstruction -> provisional evaluator`.

The shared journal stores actual received bytes, error/finish/usage and cost origin. Its usage adds logical-input and compiled-payload hashes and resolved source proofs; these are not mislabeled as raw-byte hashes. R4 rederives them from saved request/response before reference access. State transitions retain both the compact projection and the prior compiler/no_change proof. The sole quote-free no_change binds current executor event; tool events never produce authority. Tagged mutation branches (including two supersede target types) avoid unrelated NoChange branch errors.

Schema/summary/ordinary transport errors stop one track. Source, isolation, hash, ledger and budget faults retain the existing whole-round stop. Interrupted or round-fault traces verify only durable prefix and do not load reference. No retry or automatic resume. Token and invoice unknown remain unknown; historical price is fixed-control metadata, not a fresh quote or spending approval. All prior cost logic is reused without claiming byte proxies bound token or provider bills.

## Maintenance and acceptance scope

Current offline entry is `python -m scripts.s4_contract_convergence_offline` with prepare/offline/verify only. `s4_contract_convergence_acceptance` authors public-only fixed controls and delegates execution/replay; neither is another complete method runner. The old entries remain for exact historical replay. New prompts replace accumulated obsolete constraint instructions, reusing shared research rules and public-preview construction.

Acceptance is bounded single-case engineering: all methods, lookup/continuation, five incremental events, no_change, endpoint consistency, malicious/invalid anchors, ordinary failures, interruption, raw-hash preservation and clean-directory recovery. Current-version 30×3×R=3 full-batch work, unexercised real model behavior, owner semantic dispositions and formal experiment freeze remain outstanding. Fixed outputs do not yield model accuracy or improvement. References remain provisional; independent review `not_performed_skipped_by_user`; equal-configuration main block, budget matching deferred.
