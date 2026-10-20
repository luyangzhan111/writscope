"""Validate all six public requests before any possible paid model call."""

from pathlib import Path

from .artifacts import canonical_hash, digest
from .baselines import METHODS, build_request
from .contracts import load_bundle
from .providers import ModelConfig, ModelRequest


CASES = ("c001", "c002", "c003")


def prepare_m2b_requests(root: Path, model: ModelConfig, *, max_request_bytes: int) -> tuple[list[ModelRequest], dict]:
    if model.model_id == "scripted-mock-v1":
        raise ValueError("M2b requires a selected real model")
    base = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8") for method in METHODS}
    requests = []
    cases = {}
    for case in CASES:
        bundle = load_bundle(base / case)
        if bundle.review_input.case_id != case:
            raise ValueError("case directory/input ID mismatch")
        built = [build_request(bundle, method, prompts[method], model) for method in METHODS]
        if built[0].public_input != built[1].public_input or built[0].evidence_sha256 != built[1].evidence_sha256:
            raise ValueError("unequal public evidence between baselines")
        if any(len(request.model_dump_json().encode("utf-8")) > max_request_bytes for request in built):
            raise ValueError("full-context request exceeds limit; no truncation")
        requests.extend(built)
        cases[case] = {"evidence_sha256": built[0].evidence_sha256,
                       "input_json_sha256": digest((base / case / "input.json").read_bytes()),
                       "prompt_sha256": {request.method: request.prompt_sha256 for request in built}}
    if any("EVALUATOR_ONLY_M1_REFERENCE" in request.model_dump_json() for request in requests):
        raise ValueError("reference sentinel in method request")
    return requests, {"schema_version": "0.1-draft", "track": "verified_impact", "jobs": 6,
                      "model_id": model.model_id, "cases": cases,
                      "request_sha256": [digest(request.model_dump_json().encode("utf-8")) for request in requests],
                      "request_set_sha256": canonical_hash([request.model_dump() for request in requests])}
