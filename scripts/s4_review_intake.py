"""Prepare blank human review forms and compare submitted reviews offline.

No reviewer is contacted. A reviewer self-report is not proof of independence.
Reference material is accessed only by the evaluator after a response is supplied.
"""

import argparse
import json
from pathlib import Path

from scripts.s4_p2 import checked_bundle
from writscope.artifacts import digest, write_json
from writscope.contracts import parse_json
from writscope.events import JudgmentLabels


def blank(bundle_root, references, blind_packet):
    ref = parse_json(references.read_text(encoding="utf-8"))
    if ref.get("version") != "s4_p3_author_draft_v1" or len(ref["cases"]) != 30 or len(ref["pairs"]) != 12:
        raise ValueError("invalid author reference inventory")
    cases = sorted(ref["cases"])
    hashes = {case: checked_bundle(bundle_root / case).sha256 for case in cases}
    return {"version":"s4_human_review_response_v1", "status":"blank_not_submitted",
            "reviewer_id":None, "independence_declaration":None, "reviewed_at":None,
            "blind_packet_sha256":digest(blind_packet.read_bytes()), "bundle_sha256":hashes,
            "cases":{case:{"impact":None, "authorization":None, "decision":None,
                            "authorization_sources":[], "rationale":""} for case in cases},
            "pairs":{pair:{"controlled":None, "rationale":""} for pair in sorted(ref["pairs"])}}


def compare(bundle_root, references, blind_packet, response):
    ref = parse_json(references.read_text(encoding="utf-8"))
    if ref.get("version") != "s4_p3_author_draft_v1":
        raise ValueError("invalid reference version")
    if response.get("version") != "s4_human_review_response_v1" or response.get("status") != "submitted":
        raise ValueError("review response not submitted")
    if any(not isinstance(response.get(k), str) or not response[k].strip() for k in
           ("reviewer_id", "independence_declaration", "reviewed_at")):
        raise ValueError("reviewer details or self-declaration missing")
    if response.get("blind_packet_sha256") != digest(blind_packet.read_bytes()):
        raise ValueError("blind packet hash mismatch")
    if set(response.get("cases", {})) != set(ref["cases"]) or set(response.get("pairs", {})) != set(ref["pairs"]):
        raise ValueError("incomplete review inventory")
    disagreement, case_rows, pair_rows = [], [], []
    for case in sorted(ref["cases"]):
        bundle = checked_bundle(bundle_root / case)
        if response.get("bundle_sha256", {}).get(case) != bundle.sha256:
            raise ValueError("bundle hash mismatch: " + case)
        row = response["cases"][case]
        labels = JudgmentLabels.model_validate({k:row.get(k) for k in ("impact","authorization","decision")})
        if not isinstance(row.get("rationale"),str) or not row["rationale"].strip():
            raise ValueError("review rationale missing: " + case)
        sources = row.get("authorization_sources")
        if not isinstance(sources,list) or not sources:
            raise ValueError("review user sources missing: " + case)
        for source in sources:
            if set(source) != {"event_id", "quote"} or not isinstance(source["quote"],str) or not source["quote"]:
                raise ValueError("invalid review source: " + case)
            event = next((e for e in bundle.review_input.events if e.event_id == source["event_id"]), None)
            if event is None or event.role != "user" or event.provenance.authority_level != "user" or event.content.count(source["quote"]) != 1:
                raise ValueError("review source is not a unique visible user quote: " + case)
        expected = JudgmentLabels.model_validate(ref["cases"][case]["expected"])
        axes = [k for k in ("impact","authorization","decision") if getattr(labels,k) != getattr(expected,k)]
        if axes:
            disagreement.append(case)
        case_rows.append({"case_id":case, "author_reference":expected.model_dump(), "reviewer":labels.model_dump(),
                          "different_axes":axes, "rationale":row["rationale"], "sources":sources})
    for pair in sorted(ref["pairs"]):
        row = response["pairs"][pair]
        if type(row.get("controlled")) is not bool or not isinstance(row.get("rationale"),str) or not row["rationale"].strip():
            raise ValueError("pair decision or rationale missing: " + pair)
        pair_rows.append({"pair_id":pair, "controlled":row["controlled"], "rationale":row["rationale"]})
    return {"version":"s4_review_comparison_v1", "reviewer_id":response["reviewer_id"],
            "reviewed_at_self_report":response["reviewed_at"],
            "independence":"self_declared_not_independently_verified", "review_response_sha256":digest(
                json.dumps(response,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")),
            "reference_sha256":digest(references.read_bytes()), "case_count":len(case_rows),
            "cases_with_disagreement":disagreement, "pairs_flagged":[r["pair_id"] for r in pair_rows if not r["controlled"]],
            "cases":case_rows,"pairs":pair_rows,
            "disposition":"pending_human_adjudication_no_automatic_label_changes"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",choices=["blank","compare"])
    p.add_argument("--bundle-root",required=True,type=Path)
    p.add_argument("--references",required=True,type=Path)
    p.add_argument("--blind-packet",required=True,type=Path)
    p.add_argument("--response",type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    if a.output.exists(): raise ValueError("output exists")
    if a.command=="blank":
        data=blank(a.bundle_root,a.references,a.blind_packet)
    else:
        if a.response is None: p.error("compare needs --response")
        data=compare(a.bundle_root,a.references,a.blind_packet,parse_json(a.response.read_text(encoding="utf-8")))
    write_json(a.output,data)
    print(json.dumps({"status":data.get("status",data.get("disposition")),"cases":len(data["cases"])},ensure_ascii=False))


if __name__=="__main__": main()
