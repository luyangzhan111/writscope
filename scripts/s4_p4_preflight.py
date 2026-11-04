"""Create an unapproved S4-A planning draft; never read keys or price data."""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import METHODS, ROOT, checked_bundle, sources
from scripts.s4_p4 import CASES
from writscope.artifacts import canonical_hash, digest, write_json
from writscope.contracts import parse_json


def draft(bundle_root, references, disputes):
    issue = parse_json(disputes.read_text(encoding="utf-8"))
    if issue["status"] != "unresolved_not_frozen" or issue["independent_review"] != "not_received":
        raise ValueError("unexpected semantic-review state")
    bundles = {case: checked_bundle(bundle_root / case).sha256 for case in CASES}
    plan = {"version":"s4_a_experiment_draft_v1", "status":"draft_unapproved", "data_status":"not_frozen",
            "paid_execution_allowed":False, "external_transmission_allowed":False,
            "cases":CASES, "methods":list(METHODS), "bundles":bundles,
            "review_reference_sha256":digest(references.read_bytes()), "semantic_disputes_sha256":digest(disputes.read_bytes()),
            "independent_review":"not_received", "user_semantic_review":"not_received",
            "model":None, "model_version":None, "repetitions":None, "temperature":None,
            "pricing":None, "budget_cny":None, "max_provider_calls":None, "external_recipient":None,
            "external_materials":None, "thresholds":None, "budget_matched_protocol":None,
            "source_binding":sources(), "p4_sources":{p:digest((ROOT/p).read_bytes()) for p in
                ("scripts/s4_p4.py","scripts/s4_p4_scripts.py","scripts/s4_p4_report.py","scripts/s4_p4_review.py")},
            "limitations":["30-case development data only", "semantic and independent review pending",
                           "P4 scripts are not model outcomes", "no approved paid run or external transmission"]}
    return {"plan":plan, "plan_sha256":canonical_hash(plan)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root",required=True,type=Path)
    parser.add_argument("--references",required=True,type=Path)
    parser.add_argument("--disputes",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError("output exists")
    data=draft(args.bundle_root,args.references,args.disputes)
    write_json(args.output,data)
    print(json.dumps({"status":data["plan"]["status"],"paid_execution_allowed":False,"plan_sha256":data["plan_sha256"]}))


if __name__=="__main__": main()
