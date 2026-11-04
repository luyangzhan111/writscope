"""Save and replay P4 offline fault scenarios without changing normal evidence."""

import argparse
import asyncio
import json
from pathlib import Path

from scripts.s4_p4 import load_gzip, run_round, verify_round
from scripts.s4_p4_faults import change
from writscope.artifacts import digest, write_json
from writscope.contracts import parse_json


async def scenarios(bundle_root, scripts_path, output):
    if output.exists():
        raise ValueError("scenario output exists")
    output.mkdir(parents=False)
    original = parse_json(scripts_path.read_text(encoding="utf-8"))
    results = []
    for name in ("provider_error", "invalid_json", "future_lookup", "tool_forgery", "budget"):
        script_path = output / (name + "-scripts.json")
        data = original if name == "budget" else change(original, name, bundle_root)
        write_json(script_path, data)
        directory = output / name
        summary = await run_round(bundle_root, script_path, directory, 7 if name == "budget" else 260)
        replay = await verify_round(bundle_root, script_path, directory / "run.json.gz")
        if name == "budget":
            if summary["failed"] < 1 or summary["not_run"] < 1 or summary["usage"]["attempts"] != 7:
                raise ValueError("budget control did not stop the group")
        elif summary["failed"] != 1 or summary["completed"] != 89:
            raise ValueError(name + " did not isolate one failing trajectory")
        saved = load_gzip(directory / "run.json.gz")
        failed = [job for job in saved["jobs"] if job["status"] == "failed"]
        if name == "invalid_json" and not any(a["response"] == "null" for a in failed[0]["attempts"]):
            raise ValueError("invalid response was not preserved")
        if name == "future_lookup" and not any(record["status"] == "denied" and not record["events"] for record in failed[0]["lookups"]):
            raise ValueError("future lookup denial was not preserved")
        if name == "tool_forgery" and not any(not record["batch"]["accepted"] for record in failed[0]["transitions"]):
            raise ValueError("untrusted authority rejection was not preserved")
        if name != "budget" and failed[0]["final"] is not None:
            raise ValueError("fault fabricated a final decision")
        if name == "provider_error":
            inp = summary["usage"]["fields"]["input_tokens"]
            if inp["known_sum"] != "10" or inp["known_count"] != 1 or inp["total"] is not None:
                raise ValueError("failed synthetic usage was not preserved")
        results.append({"scenario": name, "summary": summary, "replay": replay["replay"],
                        "scripts_sha256": digest(script_path.read_bytes()),
                        "run_sha256": digest((directory / "run.json.gz").read_bytes())})
        write_json(output / "progress.json", {"completed_scenarios": results})
    return {"version": "s4_p4_fault_scenarios_v1", "actual_model_calls": 0, "scenarios": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = asyncio.run(scenarios(args.bundle_root.resolve(), args.scripts.resolve(), args.output.absolute()))
    write_json(args.output / "scenarios.json", data)
    print(json.dumps({"scenarios": len(data["scenarios"]), "actual_model_calls": 0}))


if __name__ == "__main__":
    main()
