# S3 development v7 offline follow-up design and implementation status

Date: 2026-10-26. This is an offline design candidate only. It does not modify
the v6 plan, prompt, saved run, raw responses, report, score, or authorization.
No provider request was made and no tests were run in this step.

## Evidence carried forward

- The three-method v3 coverage round attempted all nine case/method rows. All
  six baselines completed; all three incremental tracks stopped on extraction
  failures. There was no model-selected history lookup and no terminal result
  on those incremental rows. This satisfies attempted-coverage reporting but
  not the lookup-chain criterion.
- c004 v5 reached a real lookup and received the requested history text, but
  its continuation was rejected and no terminal review was reached.
- c004 v6 accepted four of five events, then correctly rejected the e005 grant
  because two `constraint_refs` had `field=state` while the grant subject had
  `field=status`. There was no lookup, continuation, or terminal review.
- The offline scope audit found only `c_v1_profile_status` both exactly in
  scope and required for the status impact. It also found that client-local
  mapping and “minimize changes/no new API” belong in task context, not as API
  response-field constraints. The active-state requirement does not imply
  listing every active constraint in a grant's references.

## Candidate prompt

[`incremental_interaction_v8.md`](../../../prompts/incremental_interaction_v8.md)
clarifies four interfaces for a future versioned runner:

1. Client/page mapping and implementation preferences stay in task context.
2. A server/API grant needs explicit trusted user permission for the exact
   operation and scope.
3. `constraint_refs` contains only active, exactly scope-matching constraints
   that establish an exception; other constraints remain active without being
   attached to that grant.
4. A condition dependent on hidden history remains unknown until the model
   requests that text and the returned evidence is checked. A local candidate
   digest is not a substitute for the historical fact.

At initial drafting, the prompt was not integrated or preflighted. It has since been integrated into the isolated v7 runner and offline contract; the implementation evidence is in [the offline review](s3-development-v7-offline-review.md). It has not been evaluated against a real model, so it is not evidence that the model will follow the rules. No prior output is retroactively repaired.

## S3 exit checklist

| Criterion | Current evidence | State |
| --- | --- | --- |
| Versioned incremental state, replay, strict scope/lifecycle rules | M3 and S3.4 offline evidence, including the v6 scope audit | Engineering substrate present for the covered subset; not a model correctness claim |
| Three-method common inputs and one report preserving failures | v3 nine-row real report; all rows attempted, failures retained | Coverage report exists; incremental method has no valid terminal in that round |
| Real model-selected lookup, original text return, accepted continuation, valid terminal | v5 has lookup + text return but rejected continuation; later v6 and v3 have no lookup | **Missing** |
| Author-reviewed development closure with all failures/costs preserved | Multiple exact-hash run reports and replay artifacts | Can be prepared after the missing lookup path is attempted; no all-correct threshold is required |
| Independent review for full research acceptance | Prepared blank seven-unit review package; no actual independent reviewer response | **External dependency; cannot be self-certified** |
| S4 formal comparison | No S4 frozen execution/results | Separate future stage; not part of S3 closure |

## Next required implementation and authorization boundary

The isolated v7 runner, report, preflight, and six offline controls are now prepared. The remaining next step is to refresh price and freeze a fresh exact plan before considering a real call. No model, price,
budget, or outbound-data approval is granted by this document. Any future
real attempt needs its own exact plan hash, current price check, budget ceiling,
and explicit approval of the listed data sent to DeepSeek. A failed or
non-triggered lookup remains a failure/`not_exercised`; there is no automatic
rerun.

Even if a future real diagnostic completes the lookup chain, it can close only
the author-reviewed development criterion. Full independent research
acceptance still needs an actual independent reviewer or a formally changed
acceptance standard recorded by the project owner; simulated review, self-
review, and empty forms do not count.


## Implementation update (2026-10-26)

The candidate has been integrated into a separate v7 runner/report/simulation and preflight. Ten targeted tests passed; six simulated profiles were saved and replayed. The plan remains `draft_unapproved`, its inherited price observation must be refreshed before any authorization request, and actual provider attempts remain zero. See [offline review](s3-development-v7-offline-review.md).
