"""M2a CLI: execute without labels, then independently score saved results."""

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from .artifacts import canonical_hash, digest
from .evaluation import score_run
from .providers.mock import ScriptedMock, load_script
from .reporting import write_report
from .runner import RunPolicy, run_suite


def default_output(root: Path, kind: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return root / "reports" / "local" / f"{kind}-{stamp}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="WritScope M2a no-key full-context baseline harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run two baselines with scripted responses; no labels are read")
    run.add_argument("--root", type=Path, default=Path.cwd())
    run.add_argument("--output", type=Path)
    run.add_argument("--direct-script", type=Path)
    run.add_argument("--checklist-script", type=Path)
    run.add_argument("--timeout", type=float, default=5.0)
    run.add_argument("--max-attempts-per-job", type=int, default=2)
    run.add_argument("--max-total-attempts", type=int, default=12)
    score = sub.add_parser("score", help="independently score a completed saved run")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        root = args.root.resolve()
        inputs = root / "reports" / "m1-2026-10-18" / "inputs" / "verified_impact"
        input_dirs = [inputs / case for case in ("c001", "c002", "c003")]
        prompts = {method: (root / "prompts" / (method + ".md")).read_text(encoding="utf-8") for method in ("full_direct", "full_checklist")}
        paths = {"full_direct": args.direct_script or root / "examples" / "m2a" / "direct_script.json",
                 "full_checklist": args.checklist_script or root / "examples" / "m2a" / "checklist_script.json"}
        scripts = {method: load_script(path) for method, path in paths.items()}
        providers = {method: ScriptedMock(script) for method, script in scripts.items()}
        source_files = sorted((root / "src" / "writscope").rglob("*.py"))
        source_hashes = {file.relative_to(root).as_posix(): digest(file.read_bytes()) for file in source_files}
        provenance = {"input_bundle_sha256": {case.name: digest((case / "input.json").read_bytes()) for case in input_dirs},
                      "script_sha256": {method: digest(paths[method].read_bytes()) for method in paths},
                      "script_response_sha256": {method: [digest(step.text.encode("utf-8")) for step in scripts[method].steps if step.action == "response"] for method in paths},
                      "source_tree_sha256": canonical_hash(source_hashes),
                      "environment_lock_sha256": digest((root / "uv.lock").read_bytes())}
        output = args.output or default_output(root, "m2a-run")
        policy = RunPolicy(timeout_seconds=args.timeout, max_attempts_per_job=args.max_attempts_per_job,
                           max_total_attempts=args.max_total_attempts)
        manifest = asyncio.run(run_suite(input_dirs, output, prompts, lambda method: providers[method], policy=policy, provenance=provenance))
        print(f"offline_mock completed: {manifest['run_id']} -> {output}")
        print("No real model calls. Score separately with the evaluator labels.")
    else:
        output = args.output or default_output(Path.cwd(), "m2a-score")
        summary = score_run(args.run, args.labels, output)
        write_report(summary, output / "summary.md")
        print(f"offline_mock scored: {summary['run_id']} -> {output}")


if __name__ == "__main__":
    main()
