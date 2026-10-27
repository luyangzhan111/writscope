"""Public offline controls for acceptance v4; never constructs a real provider."""
import argparse
import asyncio
import json
from pathlib import Path

from scripts import s3_acceptance_v4 as runner
from scripts import s3_acceptance_report_v4 as reporting
from scripts.m3q_fault_evidence_audit import _final
from writscope.providers import ActualUsage, ProviderResponse, ProviderError

PROFILES = ("lookup", "no_lookup", "original_rejection", "first_error", "future_lookup", "invalid_output")


class PublicControl:
    def __init__(self, root, profile):
        if profile not in PROFILES:
            raise ValueError("unknown synthetic control")
        self.root, self.profile = root, profile
        self.captured = None
        if profile == "original_rejection":
            source = root / "reports/local/s3-incremental-v3-live-20261026"
            manifest = runner.read(source / "run.json")
            if (manifest["artifacts"] != runner._artifacts(source) or manifest["plan_sha256"] !=
                    "ead6921e1716cf79bec8c2e8ee9177cbe47cbbdb465d0cb9c50f25f5e8515233"):
                raise ValueError("captured source provenance mismatch")
            rows = runner.read(source / "calls.json")
            self.captured = {row["event_seq"]: row["response_text"] for row in rows
                             if row["job_id"] == "job_002"}

    async def generate(self, request):
        public = request.public_input
        case = public["review_input"]["case_id"]
        if request.method == "incremental_review":
            value = _final(runner.bundle(self.root, case))
        else:
            event = public["review_input"]["events"][-1]
            if case == "c002" and event["seq"] == 1:
                if self.profile == "first_error":
                    raise ProviderError("offline first-track failure", retryable=False)
                if self.profile == "invalid_output":
                    return self.response("{}")
            if case == "c002" and self.captured is not None:
                return self.response(self.captured[event["seq"]])
            if case == "c004" and self.profile == "future_lookup" and event["seq"] == 1:
                value = {"kind": "lookup_request", "event_ids": ["e005"]}
            elif (case == "c004" and event["seq"] == 5 and self.profile != "no_lookup"
                  and public["history_lookup"]["attempts_used"] == 0):
                value = {"kind": "lookup_request", "event_ids": ["e002"]}
            else:
                value = {"kind": "extract", "task_context": [], "state_items": [{
                    "operation": "no_change", "source_quote": event["content"],
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
        raise ValueError("synthetic saved replay mismatch")
    summary = await reporting.report(root, preflight, output.parent / (output.name + "-report"), run_dir=output)
    if result["actual_model_calls"] != 0 or summary["metrics"] is not None:
        raise ValueError("synthetic control cannot claim model evidence")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="lookup")
    args = parser.parse_args(argv)
    result = asyncio.run(simulate(args.root, args.preflight, args.output, args.profile))
    print({key: result[key] for key in ("status", "actual_model_calls", "simulated_provider_attempts")})


if __name__ == "__main__":
    main()
