"""Offline-only candidate wire serialization. Never creates a network client or reads a key."""
from scripts.s4_prompt_candidate import STAGES
from writscope.artifacts import digest
from writscope.providers import ModelConfig, ModelRequest
from writscope.providers.deepseek import wire_bytes


def serialize(preview, max_output_tokens=4096):
    if (preview.get("version") != "s4_prompt_contract_preview_v1" or
            preview.get("status") != "draft_offline_only" or preview.get("stage") not in STAGES or
            preview.get("paid_execution_allowed") is not False or
            preview.get("external_transmission_allowed") is not False or preview.get("model") is not None):
        raise ValueError("invalid offline preview")
    stage = preview["stage"]
    config = ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=max_output_tokens)
    request = ModelRequest(method="incremental_extract" if stage == "incremental_extract" else
                           "full_checklist" if stage == "full_checklist" else "full_direct",
                           output_contract="transition_quote_v1" if stage == "incremental_extract" else "source_quote_v1",
                           model_config_values=config, instructions=preview["instructions"],
                           prompt_sha256=preview["prompt_sha256"], evidence_sha256=preview["evidence_sha256"],
                           public_input=preview["public_input"], output_schema=preview["output_schema"])
    if digest(request.instructions.encode("utf-8")) != request.prompt_sha256:
        raise ValueError("prompt binding mismatch")
    wire = wire_bytes(request)
    return {"version": "s4_wire_preview_v1", "execution_kind": "offline_serialization_only",
            "stage": stage, "wire_sha256": digest(wire), "wire_bytes": len(wire),
            "actual_model_calls": 0, "provider_selected_for_experiment": False,
            "paid_execution_allowed": False, "external_transmission_allowed": False}
