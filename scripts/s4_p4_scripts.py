"""Create deliberately uninformative mock responses from public bundles only."""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import METHODS, checked_bundle
from writscope.artifacts import safe_path, write_json


def generate(bundle_root, cases):
    jobs = {}
    for case in cases:
        bundle = checked_bundle(safe_path(bundle_root, case))
        events = bundle.review_input.events
        source = next(e for e in events if e.role == "user")
        quote = {"event_id": source.event_id, "quote": source.content}
        breaking = bundle.evidence.assessment == "breaking"
        if bundle.evidence.assessment not in {"breaking", "compatible"}:
            raise ValueError("unhandled public impact")
        # This intentionally uninformative response is not a reference answer.
        rows = [{"impact_id": impact.impact_id, "verdict": "not_covered", "constraint_refs": [],
                 "authorization_refs": [], "source_refs": [quote], "reason": "synthetic transport fixture"}
                for impact in bundle.evidence.impacts]
        def final(checklist):
            return {"impact": bundle.evidence.assessment, "authorization": "not_covered" if breaking else "not_needed",
                    "decision": "revise" if breaking else "allow",
                    "affected_subjects": [i.subject.model_dump(mode="json") for i in bundle.evidence.impacts],
                    "per_impact_authorization": rows, "constraint_refs": [], "authorization_refs": [],
                    "source_refs": [quote], "evidence_refs": ["impact"], "reason": "synthetic transport fixture",
                    "limitations": ["not a model result"], "derived_items": [],
                    "checklist": {k: "synthetic fixture" for k in ("reconstruction", "subject_check", "version_check", "operation_check", "conditions_check", "lifecycle_check")} if checklist else None}
        for method in METHODS:
            steps = []
            if method == "incremental":
                for event in events:
                    item = {"operation": "no_change", "source_quote": event.content, "target_ids": [],
                            "constraint": None, "authorization": None, "value_scope": None}
                    steps.append({"kind": "batch", "items": [item]})
                steps.append({"kind": "final", "payload": final(False)})
            else:
                steps.append({"kind": "final", "payload": final(method == "full_checklist")})
            # Every method gets the same bounded lookup service. This scripted
            # control exercises it once per method for a single public case.
            if case == cases[0]:
                steps.insert(0, {"kind": "lookup", "event_ids": [events[0].event_id]})
            jobs[case + "/" + method] = {"schema_version": "0.1-draft", "execution_kind": "offline_mock",
                "description": "Uniform synthetic transport fixture; not a model response or reference label.",
                "steps": [{"action": "response", "text": json.dumps(step, ensure_ascii=False, separators=(",", ":")),
                           "response_ref": None, "delay_seconds": 0.0, "synthetic_usage": None} for step in steps]}
    return {"version": "s4_p2_scripts_v1", "jobs": jobs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    cases = [f"s4_{n:03d}" for n in range(1, 31)]
    write_json(args.output, generate(args.bundle_root, cases))
    print(json.dumps({"scripted_jobs": len(cases) * len(METHODS), "actual_model_calls": 0}))


if __name__ == "__main__":
    main()
