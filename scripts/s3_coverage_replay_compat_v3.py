"""Read-only replay/report adapter for nullable failures in coverage v3 jobs."""
import argparse
import asyncio
from pathlib import Path

from scripts import s3_coverage_v3 as runner
from scripts import s3_coverage_report_v3 as reporting
from writscope.artifacts import digest, write_json


class SavedJob(dict):
    """Treat serialized null failure as the verifier's missing-value default."""
    def get(self, key, default=None):
        value = super().get(key, default)
        return default if key == "failure" and value is None else value


def install_compat_read():
    original = runner.read

    def compatible_read(path):
        value = original(path)
        if Path(path).name == "jobs.json" and isinstance(value, list):
            return [SavedJob(row) for row in value]
        return value

    runner.read = compatible_read
    return original


async def replay_and_report(root, preflight, run_dir, report_dir, evidence_dir):
    if any(path.resolve().parent != root.resolve() / "reports/local"
           for path in (report_dir, evidence_dir)):
        raise ValueError("outputs must be direct children of reports/local")
    original_read = install_compat_read()
    try:
        manifest = await runner.verify(root, preflight, run_dir)
        summary = await reporting.report(root, preflight, report_dir, run_dir=run_dir)
        evidence_dir.mkdir(parents=True, exist_ok=False)
        write_json(evidence_dir / "verification.json", {
            "version": "s3_coverage_replay_compat_v3",
            "execution_kind": "offline_verified_saved_real_run",
            "run_manifest_sha256": digest((run_dir / "run.json").read_bytes()),
            "runner_source_sha256": digest(Path(runner.__file__).read_bytes()),
            "compatibility_source_sha256": digest(Path(__file__).read_bytes()),
            "status": manifest["status"], "actual_model_calls": manifest["actual_model_calls"],
            "saved_report_summary_sha256": digest((report_dir / "summary.json").read_bytes()),
            "saved_report_sha256": digest((report_dir / "report.md").read_bytes()),
            "report_metrics": summary["metrics"], "new_model_calls": 0,
            "raw_nullable_failure_fields_preserved": True,
            "explanation": "The frozen verifier calls job.get('failure', {}).get(...), but completed jobs serialize failure=null. This adapter changes only the in-memory verifier read view and never writes to run artifacts."
        })
        return manifest, summary
    finally:
        runner.read = original_read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest, summary = asyncio.run(replay_and_report(
        args.root.resolve(), args.preflight.resolve(), args.run.resolve(),
        args.report.resolve(), args.evidence.resolve()))
    print({"status": manifest["status"], "actual_model_calls": manifest["actual_model_calls"],
           "metrics": summary["metrics"], "new_model_calls": 0})


if __name__ == "__main__":
    main()
