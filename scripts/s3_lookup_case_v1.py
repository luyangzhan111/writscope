"""Public synthetic lookup dependency for S3 development; no evaluator inputs."""
from pathlib import Path

from writscope.artifacts import digest
from writscope.contracts import PublicBundle, load_bundle


VERSION = "s3_lookup_dependency_case_v1"


def build(root: Path, *, matching_candidate: bool = False) -> PublicBundle:
    original = load_bundle(root / "reports/m1-2026-10-18/inputs/verified_impact/c002")
    value = original.model_dump()
    review = value["review_input"]
    review["case_id"] = "c004"
    reported = (review["candidate_patch"]["sha256"] if matching_candidate else
                digest(b"WritScope public synthetic alternate candidate registration v1"))
    review["events"][1]["content"] = (
        "候选登记报告：本次登记的候选补丁 SHA-256 为 " + reported +
        "。该摘要只标识这份登记候选；本报告不表达用户许可。")
    review["events"][1]["provenance"]["artifact_refs"] = []
    review["events"][4]["content"] = (
        "允许本次将 GET /api/v1/profile 的 status 替换为 state，字段值及含义必须保持不变；"
        "许可仅覆盖 e002 工具报告中 SHA-256 标识的那一份候选补丁。其他候选不在许可范围内。")
    return PublicBundle.model_validate(value)
