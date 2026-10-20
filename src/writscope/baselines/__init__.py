"""Equal-evidence request construction. No filesystem or evaluator access."""

from ..artifacts import canonical_hash, digest
from ..contracts import PublicBundle, ReviewPayload
from ..providers import ModelConfig, ModelRequest

METHODS = ("full_direct", "full_checklist")


def build_request(bundle: PublicBundle, method: str, instructions: str, config: ModelConfig) -> ModelRequest:
    if method not in METHODS:
        raise ValueError("unknown baseline")
    if not instructions.strip():
        raise ValueError("empty method instructions")
    public = {
        "review_input": bundle.review_input.model_dump(),
        "artifacts": dict(bundle.contents),
        "artifact_registry": bundle.registry.model_dump(),
    }
    return ModelRequest(
        method=method, model_config_values=config.model_copy(deep=True),
        instructions=instructions, prompt_sha256=digest(instructions.encode("utf-8")),
        evidence_sha256=canonical_hash(public), public_input=public,
        output_schema=ReviewPayload.model_json_schema(),
    )
