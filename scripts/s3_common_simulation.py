"""Public scripted controls for the common runner; never model predictions."""
import argparse
import asyncio
import json
from pathlib import Path

from scripts import m3q_fault_evidence_audit as fixtures
from scripts import s3_common_round as common
from scripts import s3_common_report as reporting
from writscope.providers import ActualUsage, ProviderResponse, ProviderError


class PublicControlProvider:
    def __init__(self, root, first_error=False):
        self.root, self.first_error = root, first_error
        self.incremental = {case: common.helper.SimulatedActualProvider(
            fixtures._basic_script(common.bundle(root, case))) for case in common.CASES}

    async def generate(self, request):
        if self.first_error:
            raise ProviderError("offline first-provider failure control")
        case = request.public_input["review_input"]["case_id"]
        if request.method.startswith("incremental"):
            return await self.incremental[case].generate(request)
        value = fixtures._final(common.bundle(self.root, case))
        if request.method == "full_checklist":
            value["checklist"] = {key: "scripted transport control, not a model answer" for key in (
                "reconstruction", "subject_check", "version_check", "operation_check", "conditions_check", "lifecycle_check")}
        return ProviderResponse(text=json.dumps(value, ensure_ascii=False), finish_reason="stop",
            actual_usage=ActualUsage(input_tokens=20, output_tokens=10,
                                     usage_source="provider_response", cost_source="unavailable"))


async def simulate(root, prepared, output, first_error=False):
    result = await common.execute(root, prepared, output, PublicControlProvider(root, first_error))
    if await common.verify(root, prepared, output) != result:
        raise ValueError("saved simulation replay mismatch")
    report_dir = output.parent / (output.name + "-report")
    summary = await reporting.report(root, prepared, report_dir, run_dir=output)
    if result["actual_model_calls"] or summary["metrics"] is not None:
        raise ValueError("scripted control must not claim model evidence")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-error", action="store_true")
    args = parser.parse_args(argv)
    result = asyncio.run(simulate(args.root, args.preflight, args.output, args.first_error))
    print(json.dumps({key: result[key] for key in ("status", "actual_model_calls", "simulated_provider_attempts")}))


if __name__ == "__main__":
    main()
