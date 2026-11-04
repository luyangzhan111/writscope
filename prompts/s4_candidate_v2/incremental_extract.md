For this stage use kind="batch" and one of the operation-specific item shapes in output_schema. All item fields are required, including explicit null payloads and value_scope.

- add_constraint: non-null constraint, authorization=null, target_ids=[], constraint.supersedes=[], value_scope=null.
- grant: constraint=null, non-null authorization, target_ids=[], authorization.supersedes=[]. Never copy a referenced constraint object into this item; use authorization.constraint_refs for applicable already-active or earlier-in-batch constraint IDs.
- supersede: exactly one of constraint/authorization is non-null; target_ids is nonempty and exactly equals that new payload's supersedes list, in the same order.
- revoke and mark_ambiguous: constraint=null, authorization=null, nonempty unique target_ids, value_scope=null. revoke targets authorizations; do not use it to revoke a constraint.
- no_change: constraint=null, authorization=null, target_ids=[], value_scope=null; it must be the batch's only item.

value_scope can be non-null only for a matching value-semantics authorization, with the same exact single subject, old_field and quote-grounded old/new values. No extra fields, manufactured IDs or unsupported condition evidence. Candidate-hash conditions use kind=candidate_sha256 and public artifact IDs for evidence_refs, not event IDs. Other condition types do not have an independent verifier; never mark their truth as proof of permission.

Do not infer a server grant from permission to perform client-local mapping. Record only independently justified constraint/grant operations; context-only requirements may remain in the unchanged visible history. Never drop an invalid field, split a malformed item, or repair a response after the fact; return a valid shape from the outset.
