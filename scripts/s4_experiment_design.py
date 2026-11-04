"""Compute a non-authorizing, offline S4-A design from public cases."""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import METHODS, ROOT, checked_bundle, sources
from scripts.s4_p4 import CASES
from writscope.artifacts import canonical_hash, digest, write_json


def design(bundle_root):
    bundles = {case:checked_bundle(bundle_root / case) for case in CASES}
    rows = []
    for case, bundle in bundles.items():
        events = len(bundle.review_input.events)
        # P2 exposes the same lookup service to all methods, permits one
        # continuation per stage and at most three lookups per trajectory.
        baseline_cap = 1 + min(3, 1)
        incremental_stages = events + 1
        incremental_cap = incremental_stages + min(3, incremental_stages)
        rows.append({"case_id":case,"events":events,"baseline_per_method_cap":baseline_cap,
                     "incremental_cap":incremental_cap,
                     "total_cap_per_repeat":2*baseline_cap+incremental_cap,
                     "public_bundle_sha256":bundle.sha256})
    per_repeat = sum(row["total_cap_per_repeat"] for row in rows)
    pilot_cases = ["s4_004", "s4_006", "s4_010", "s4_027"]
    pilot_cap = sum(row["total_cap_per_repeat"] for row in rows if row["case_id"] in pilot_cases)
    proposal = {"version":"s4_a_comparison_design_candidate_v1", "status":"draft_unapproved",
        "data_status":"not_frozen", "paid_execution_allowed":False, "external_transmission_allowed":False,
        "purpose":"development comparison; not independent generalization evidence", "track":"A_verified_impact",
        "case_count":len(rows), "methods":list(METHODS),
        "primary_candidate":{"repetitions":3,"trajectories":len(rows)*len(METHODS)*3,
                             "max_provider_attempts":per_repeat*3,"status":"proposal_not_approved"},
        "separate_connectivity_pilot":{"cases":pilot_cases,"repetitions":1,"trajectories":len(pilot_cases)*len(METHODS),
                                      "max_provider_attempts":pilot_cap,"status":"optional_separate_plan_not_approved"},
        "counts_per_repeat":{"events":sum(r["events"] for r in rows),"trajectories":len(rows)*len(METHODS),
                             "max_provider_attempts":per_repeat,"cases":rows},
        "call_cap_basis":{"baseline_stages":1,"incremental_stages":"events + 1 final",
                          "lookup_limit_per_trajectory":3,"max_continuations_per_stage":1,"automatic_retries":0,
                          "constraint":"max lookups cannot exceed number of stages"},
        "comparison_blocks":{"equal_configuration":"primary proposed; actual cost reported per method",
                             "equal_budget":"separate unfrozen block; rules and total resources require explicit proposal"},
        "model":None,"provider":None,"version_or_snapshot":None,"temperature_candidate":0.0,
        "output_token_limit":None,"context_limit":None,"seed_and_order":None,"cache_pricing_policy":None,
        "quality_tolerance":None,"false_allow_tolerance":None,"false_block_tolerance":None,
        "cost_benefit_threshold":None,"official_price":None,"exchange_rate":None,"local_budget_cny":None,
        "outbound_material_scope":None,"provider_bill_hard_cap":None,
        "review_status":"unresolved_user_and_independent_review_pending",
        "real_provider_runner":"not_implemented_for_p2_p4", "real_scoring_report":"not_implemented",
        "p2_incremental_final_mismatch_policy":"currently marks disagreement invalid; formal policy must be frozen and assessed for fairness",
        "source_binding":sources(),"design_source_sha256":digest((ROOT/'scripts/s4_experiment_design.py').read_bytes()),
        "limitations":["R=3 is a proposal, not a user authorization", "repeats do not create independent scenarios",
            "provider limits, pricing, context and maximum request bytes are unverified",
            "the cap assumes the P2 stage and lookup policies; any change requires recomputation",
            "no independent semantic review or approved real invocation"]}
    return {"plan":proposal,"plan_sha256":canonical_hash(proposal)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle-root",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    if a.output.exists(): raise ValueError("output exists")
    data=design(a.bundle_root)
    write_json(a.output,data)
    print(json.dumps({"status":data["plan"]["status"],"per_repeat_cap":data["plan"]["counts_per_repeat"]["max_provider_attempts"],
                      "proposed_three_repeat_cap":data["plan"]["primary_candidate"]["max_provider_attempts"],
                      "paid_execution_allowed":False}))


if __name__=="__main__": main()
