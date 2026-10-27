"""Offline adapter controls for the V2 real boundary, never paid execution."""
import argparse
import asyncio
from pathlib import Path

from scripts import s3_common_real_v2 as runner
from scripts import s3_common_real_v2_report as reporting
from scripts import s3_common_offline_v2 as fixtures
from writscope.providers import ActualUsage, ProviderResponse
from writscope.providers.mock import ScriptedMock

FAULTS = ("none", "timeout", "finish", "over_output", "invalid_usage", "unexpected_error")


class PublicSimulationProvider:
    def __init__(self, root, profile="success_lookup", fault="none"):
        if fault not in FAULTS:
            raise ValueError("unknown offline fault")
        self.fault = fault
        self.providers = {(case, method): ScriptedMock(fixtures.job_script(
            runner.bundle(root, case), method, profile)) for case in runner.CASES for method in runner.METHODS}

    async def generate(self, request):
        if self.fault == "timeout":
            raise TimeoutError("offline deadline injection")
        if self.fault == "unexpected_error":
            raise RuntimeError("offline unexpected error")
        case = request.public_input["review_input"]["case_id"]
        method = "incremental" if request.method.startswith("incremental") else request.method
        response = await self.providers[case, method].generate(request)
        usage = response.synthetic_usage
        actual = (ActualUsage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                              usage_source="provider_response", cost_source="unavailable") if usage else None)
        if self.fault == "over_output":
            actual = actual.model_copy(update={"output_tokens": 4097})
        elif self.fault == "invalid_usage":
            actual = actual.model_copy(update={"input_tokens": -1})
        return ProviderResponse(text=response.text, actual_usage=actual,
                                finish_reason="length" if self.fault == "finish" else "stop")


async def simulate(root, prepared, output, profile="success_lookup", fault="none"):
    result = await runner.execute(root, prepared, output, PublicSimulationProvider(root, profile, fault))
    if await runner.verify(root, prepared, output) != result:
        raise ValueError("V2 real-boundary simulation replay mismatch")
    report = await reporting.report(root, prepared, output.parent/(output.name+"-report"), run_dir=output)
    if result["actual_model_calls"] != 0 or report["metrics"] is not None:
        raise ValueError("simulation cannot claim real model evidence")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=fixtures.PROFILES, default="success_lookup")
    parser.add_argument("--fault", choices=FAULTS, default="none")
    args = parser.parse_args(argv)
    result = asyncio.run(simulate(args.root, args.preflight, args.output, args.profile, args.fault))
    print({key: result[key] for key in ("status", "actual_model_calls", "simulated_provider_attempts")})


if __name__ == "__main__":
    main()
