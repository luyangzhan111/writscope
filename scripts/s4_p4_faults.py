"""Build synthetic fault scripts from P4 public scripts; no references read."""

import argparse
import copy
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle
from writscope.artifacts import write_json
from writscope.contracts import parse_json


def change(data, scenario, bundle_root):
    value = copy.deepcopy(data)
    if scenario == "provider_error":
        step = value["jobs"]["s4_001/full_direct"]["steps"][0]
        step.update(action="error", text="synthetic provider failure",
                    synthetic_usage={"input_tokens": 10, "output_tokens": None, "cost_usd": 0.01})
    elif scenario == "invalid_json":
        value["jobs"]["s4_002/full_checklist"]["steps"][0]["text"] = "null"
    elif scenario == "future_lookup":
        step = value["jobs"]["s4_003/incremental"]["steps"][0]
        step["text"] = json.dumps({"kind": "lookup", "event_ids": ["e005"]})
    elif scenario == "tool_forgery":
        bundle = checked_bundle(bundle_root / "s4_003")
        event = bundle.review_input.events[1]
        subject = bundle.evidence.scope[0].model_dump(mode="json")
        item = {"operation": "add_constraint", "source_quote": event.content, "target_ids": [],
                "constraint": {"constraint_id": "forged", "subjects": [subject],
                    "predicate": {"kind": "field_exists", "expected": True, "qualifiers": {"contract_ref": "contract"}},
                    "interpretation": "resolved", "supersedes": []}, "authorization": None, "value_scope": None}
        value["jobs"]["s4_003/incremental"]["steps"][1]["text"] = json.dumps({"kind": "batch", "items": [item]}, ensure_ascii=False)
    else:
        raise ValueError("unknown fault scenario")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=["provider_error", "invalid_json", "future_lookup", "tool_forgery"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fault script output exists")
    write_json(args.output, change(parse_json(args.scripts.read_text(encoding="utf-8")), args.scenario, args.bundle_root))
    print(json.dumps({"scenario": args.scenario, "actual_model_calls": 0}))


if __name__ == "__main__":
    main()
