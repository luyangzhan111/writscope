"""Versioned error propagation adapter; no provider entry or policy changes.

The frozen v2 runner remains replayable. This adapter may be integrated into a
future, separately frozen runner; importing it does not change the old Cursor.
"""
from scripts import s3_common_real_v2 as frozen


VERSION = "s3_failure_summary_v1"


def state_rejection_detail(call, result):
    """Use only the unique rejected step linked to this exact core call.

Never borrow an earlier event's error or infer a reason from model prose.
Missing or inconsistent evidence stays missing.
    """
    if call.get("status") != "state_rejected" or call.get("error") is not None:
        return None
    steps = [step for step in result.get("steps", [])
             if call.get("call_id") in step.get("call_ids", [])
             and call.get("event_seq") == step.get("seq")]
    if len(steps) != 1:
        return None
    step = steps[0]
    if step.get("status") not in {"batch_rejected", "context_rejected"}:
        return None
    reason = step.get("error")
    if not isinstance(reason, str) or not reason.strip():
        return None
    record = step.get("batch_record")
    if step["status"] == "batch_rejected":
        if not record or record.get("accepted") is not False or record.get("rejection_reason") != reason:
            return None
    return {"error": reason, "core_call_id": call["call_id"],
            "event_seq": step["seq"], "event_id": step["event_id"],
            "transition_status": step["status"],
            "failed_item_index": record.get("failed_item_index") if record else None,
            "source": "matching_transition_step"}


class FailureSummaryCursor(frozen.Cursor):
    """Change only the missing error summary after unchanged consumption."""

    async def consume(self, row, plan):
        status, error = await super().consume(row, plan)
        if (self.unit["method"] != "incremental" or status != "state_rejected"
                or error is not None or not self.steps):
            return status, error
        call = self.result["calls"][len(self.steps)-1]
        detail = state_rejection_detail(call, self.result)
        if detail is not None:
            error = detail["error"]
            self.failure = {**self.failure, "error": error}
        return status, error
