"""Public-evidence citation syntax check, not semantic support adjudication."""
import json
from scripts.s4_p2 import checked_bundle
from writscope.contracts import validate_output


def check(bundle_path, method, payload):
    if method not in {"full_direct", "full_checklist", "incremental"}:
        raise ValueError("unknown method")
    bundle = checked_bundle(bundle_path)
    result = validate_output(json.dumps(payload, ensure_ascii=False), bundle,
                             "full_checklist" if method == "full_checklist" else "full_direct",
                             output_contract="source_quote_v1")
    refs = list(result.source_refs)
    refs.extend(ref for item in result.derived_items for ref in item.source_refs)
    refs.extend(ref for impact in result.per_impact_authorization for ref in impact.source_refs)
    return {"version": "s4_citation_quality_v1", "status": "syntactically_valid",
            "parsed_citations": len(refs), "total_citations": len(refs),
            "semantic_support": "not_assessed", "actual_model_calls": 0}
