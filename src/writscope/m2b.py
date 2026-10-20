"""DeepSeek M2b connection pilot. Nothing calls the API without `run`."""

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path

from .artifacts import canonical_hash, digest, write_json
from .budget import PriceCeiling, RealBudgetPolicy, ReservationLedger
from .evaluation import score_run
from .preflight import CASES, prepare_m2b_requests
from .providers import ModelConfig
from .providers.deepseek import DeepSeekProvider, MODEL_ID, wire_bytes
from .reporting import write_report
from .runner import RunPolicy, run_suite


AUTHORIZED_MAX_CNY = Decimal("20")
PRICE_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


def policy(max_cny: Decimal) -> RealBudgetPolicy:
    if max_cny > AUTHORIZED_MAX_CNY:
        raise ValueError("this M2b pilot is authorized for at most 20 CNY")
    return RealBudgetPolicy(
        max_total_cny=max_cny, max_provider_attempts=6, max_attempts_per_job=1,
        timeout_seconds=90, max_output_tokens=4096, concurrency=1,
        price_ceiling=PriceCeiling(input_cny_per_million=Decimal("2"),
                                   output_cny_per_million=Decimal("8"),
                                   source_url=PRICE_URL,
                                   checked_at_utc="2026-10-20T02:30:00Z"),
    )


def prepare(root: Path, budget: RealBudgetPolicy):
    model = ModelConfig(model_id=MODEL_ID, temperature=0.0,
                        max_output_tokens=budget.max_output_tokens)
    requests, manifest = prepare_m2b_requests(root, model, max_request_bytes=budget.max_request_bytes)
    ledger = ReservationLedger(budget)
    quotes = [ledger.quote(len(wire_bytes(request))) for request in requests]
    if sum(quotes, Decimal("0")) > budget.max_total_cny:
        raise ValueError("authorized budget cannot reserve all six planned requests")
    manifest.update({"execution_kind": "preflight_no_model_call", "provider": "deepseek",
                     "endpoint": "https://api.deepseek.com/chat/completions", "currency": "CNY",
                     "authorized_max_cny": str(budget.max_total_cny),
                     "peak_price_ceiling": budget.price_ceiling.model_dump(mode="json"),
                     "request_wire_bytes": [len(wire_bytes(request)) for request in requests],
                     "per_call_estimated_reservation_cny": [str(value) for value in quotes],
                     "six_call_estimated_reservation_cny": str(sum(quotes, Decimal("0"))),
                     "budget_note": "local conservative estimate, not a provider billing guarantee"})
    source_hashes = {file.relative_to(root).as_posix(): digest(file.read_bytes())
                     for file in sorted((root / "src" / "writscope").rglob("*.py"))}
    manifest["source_tree_sha256"] = canonical_hash(source_hashes)
    manifest["environment_lock_sha256"] = digest((root / "uv.lock").read_bytes())
    return requests, manifest, model


def default_output(root: Path, kind: str) -> Path:
    return root / "reports" / "local" / f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="DeepSeek M2b six-job connection pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight", help="validate six requests and budget with no network or key")
    run = sub.add_parser("run", help="make at most six real requests using DEEPSEEK_API_KEY")
    for command in (preflight, run):
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument("--output", type=Path)
        command.add_argument("--max-cny", type=Decimal, default=AUTHORIZED_MAX_CNY)
    score = sub.add_parser("score", help="independently score a completed saved run")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "score":
        output = args.output or default_output(Path.cwd(), "m2b-score")
        summary = score_run(args.run, args.labels, output)
        write_report(summary, output / "summary.md")
        print(f"{summary['execution_kind']} scored: {summary['run_id']} -> {output}")
        return
    root = args.root.resolve()
    budget = policy(args.max_cny)
    requests, preflight_manifest, model = prepare(root, budget)
    output = args.output or default_output(root, "m2b-" + args.command)
    if args.command == "preflight":
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "preflight.json", preflight_manifest)
        print(f"M2b preflight only: six requests; estimated reservation {preflight_manifest['six_call_estimated_reservation_cny']} CNY -> {output}")
        print("No key read, no network or model call.")
        return
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is not set; no model call was made")
    provider = DeepSeekProvider(key, budget.price_ceiling)
    input_root = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
    prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8")
               for method in ("full_direct", "full_checklist")}
    provenance = {"provider": "deepseek", "endpoint": preflight_manifest["endpoint"],
                  "model_id": MODEL_ID, "preflight": preflight_manifest,
                  "source_tree_sha256": preflight_manifest["source_tree_sha256"],
                  "environment_lock_sha256": preflight_manifest["environment_lock_sha256"],
                  "credential_source": "DEEPSEEK_API_KEY_env_name_only"}
    runner_policy = RunPolicy(execution_kind="real_model", timeout_seconds=budget.timeout_seconds,
                              max_attempts_per_job=budget.max_attempts_per_job,
                              max_total_attempts=budget.max_provider_attempts,
                              max_request_bytes=budget.max_request_bytes,
                              max_response_bytes=budget.max_response_bytes)
    manifest = asyncio.run(run_suite([input_root / case for case in CASES], output, prompts,
                                     lambda method: provider, policy=runner_policy, model=model,
                                     provenance=provenance, ledger=ReservationLedger(budget),
                                     wire_size=lambda request: len(wire_bytes(request)),
                                     expected_request_hashes=preflight_manifest["request_sha256"]))
    print(f"real_model pilot finished: {manifest['run_id']} -> {output}")
    print("Score separately; do not interpret six development jobs as a formal comparison.")


if __name__ == "__main__":
    main()
