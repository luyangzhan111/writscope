"""Cross-check shared mock execution before exercising offline metrics wiring."""
from scripts.s4_metrics_pipeline_offline import check as check_metrics
from scripts.s4_shared_repeat_offline import verify


def check(shared_run, transport, scripted_run, bundle_root, scripts_path, reference_path, disposition_path):
    shared = verify(shared_run, transport)
    if (scripted_run.get("execution_kind") != "offline_mock" or scripted_run.get("actual_model_calls") != 0 or
            scripted_run.get("schedule") != shared_run["schedule"] or scripted_run.get("rows") != shared_run["rows"] or
            len(scripted_run.get("rounds", [])) != len(shared_run["rounds"]) or
            any(a["jobs"] != b["jobs"] for a, b in zip(scripted_run["rounds"], shared_run["rounds"]))):
        raise ValueError("shared and scripted trajectories differ")
    # The legacy metrics entry independently rebuilds and verifies the scripted
    # run before reading any reference. Both paths remain mock-only.
    metrics = check_metrics(bundle_root, scripts_path, scripted_run, reference_path, disposition_path)
    return {"version": "s4_shared_metrics_offline_v1", "execution_kind": "offline_simulation",
            "shared_transport": shared, "evaluation_wiring": metrics,
            "actual_model_calls": 0, "accuracy": None}
