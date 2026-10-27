"""Public offline controls for the interaction v8/final v2 c004 runner; no real provider."""
import argparse
import asyncio
import json
from pathlib import Path
import re

from scripts import s3_acceptance_v8 as runner
from scripts import s3_acceptance_report_v8 as reporting
from scripts.m3q_fault_evidence_audit import _final
from writscope.providers import ActualUsage, ProviderResponse, ProviderError

PROFILES = ("v8_lookup", "no_lookup", "original_v4_rejection", "saved_v5_bad_continuation", "first_error", "invalid_output")


class PublicControl:
    def __init__(self, root, profile):
        if profile not in PROFILES:
            raise ValueError("unknown offline control")
        self.root, self.profile = root, profile
        self.captured = None
        if profile == "original_v4_rejection":
            path = root / "reports/local/s3-development-v4-live-20261026/calls.json"
            rows = runner.read(path)
            self.captured = next(row["response_text"] for row in rows
                                 if row["job_id"] == "job_002")
        elif profile == "saved_v5_bad_continuation":
            path = root / "reports/local/s3-development-v5-live-20261026/calls.json"
            rows = runner.read(path)
            self.captured = next(row["response_text"] for row in rows
                                 if row["stage"] == "lookup_continuation")

    async def generate(self, request):
        public = request.public_input
        data = runner.bundle(self.root, "c004")
        if request.method == "incremental_review":
            return self.response(json.dumps(_final(data), ensure_ascii=False))
        event = public["review_input"]["events"][-1]
        seq = event["seq"]
        if self.profile == "first_error" and seq == 1:
            raise ProviderError("offline first-track failure", retryable=False)
        if self.profile == "invalid_output" and seq == 1:
            return self.response("{}")
        if self.profile == "original_v4_rejection" and seq == 1:
            return self.response(self.captured)
        if seq == 1:
            content = event["content"]
            constraint_quote = "GET /api/v1/profile 的旧客户端契约是 HTTP 200，响应对象只能包含 status，值为字符串 active，表示账户启用。"
            mapping_quote = "可以在客户端本地映射成页面需要的 state。"
            value = {"kind": "extract",
                     "task_context": [{"source_quote": mapping_quote, "verification_refs": []}],
                     "state_items": [{"source_quote": constraint_quote, "target_ids": [],
                                      "constraint": {
                                          "constraint_id": "c_v1_profile_status_contract",
                                          "subjects": [{"protocol": "http", "method": "GET",
                                                        "path": "/api/v1/profile", "api_version": "v1",
                                                        "location": "response.body", "field": "status"}],
                                          "predicate": {"kind": "response_shape", "expected": {"status": "active"},
                                                        "qualifiers": {"http_status": 200,
                                                                       "exact_keys": ["status"],
                                                                       "status_type": "string"}},
                                          "interpretation": "resolved", "supersedes": []},
                                      "authorization": None, "operation": "add_constraint"}]}
        elif seq in (2, 3):
            value = {"kind": "extract", "task_context": [],
                     "state_items": [{"operation": "no_change", "source_quote": event["content"],
                                      "target_ids": [], "constraint": None, "authorization": None}]}
        elif seq == 4:
            value = {"kind": "extract", "task_context": [],
                     "state_items": [{"operation": "no_change", "source_quote": event["content"],
                                      "target_ids": [], "constraint": None, "authorization": None}]}
        elif seq == 5 and self.profile != "no_lookup" and not public["history_lookup"]["attempts_used"]:
            value = {"kind": "lookup_request", "event_ids": ["e002"]}
        elif seq == 5 and public["history_lookup"]["attempts_used"] and self.profile == "saved_v5_bad_continuation":
            return self.response(self.captured)
        elif seq == 5 and public["history_lookup"]["attempts_used"]:
            lookup = public["history_lookup"]["current_event_results"][0]["events"][0]["content"]
            match = re.search(r"[0-9a-f]{64}", lookup)
            if match is None:
                raise ValueError("synthetic lookup report has no digest")
            value = {"kind": "extract", "task_context": [], "state_items": [{
                "source_quote": event["content"], "target_ids": [], "constraint": None,
                "authorization": {
                    "authorization_id": "a_c004_conditional_v7_control",
                    "subjects": [{"protocol": "http", "method": "GET", "path": "/api/v1/profile",
                                  "api_version": "v1", "location": "response.body", "field": "status"}],
                    "change_type": "replace_field",
                    "change_details": {"old_field": "status", "new_field": "state",
                                        "preserve_value_semantics": True},
                    "conditions": [{"kind": "candidate_sha256", "value": match.group(0),
                                    "resolution": "unknown", "evidence_refs": ["patch"]}],
                    "interpretation": "resolved", "constraint_refs": ["c_v1_profile_status_contract"],
                    "supersedes": []}, "operation": "grant"}]}
        else:
            value = {"kind": "extract", "task_context": [],
                     "state_items": [{"operation": "no_change", "source_quote": event["content"],
                                      "target_ids": [], "constraint": None, "authorization": None}]}
        return self.response(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def response(text):
        usage = ActualUsage(input_tokens=20, output_tokens=10,
                            usage_source="provider_response", cost_source="unavailable")
        return ProviderResponse(text=text, actual_usage=usage, finish_reason="stop")


async def simulate(root, preflight, output, profile):
    result = await runner.execute(root, preflight, output, PublicControl(root, profile))
    if await runner.verify(root, preflight, output) != result:
        raise ValueError("offline saved replay mismatch")
    summary = await reporting.report(root, preflight, output.parent / (output.name + "-report"), run_dir=output)
    if result["actual_model_calls"] != 0 or summary["metrics"] is not None:
        raise ValueError("offline control cannot claim model evidence")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="v8_lookup")
    args = parser.parse_args(argv)
    result = asyncio.run(simulate(args.root, args.preflight, args.output, args.profile))
    print({key: result[key] for key in ("status", "actual_model_calls", "simulated_provider_attempts")})


if __name__ == "__main__":
    main()
