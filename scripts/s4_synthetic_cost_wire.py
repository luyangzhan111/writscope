"""Derive mock provider wire bytes and enforce a synthetic cost envelope."""
from scripts.s4_prompt_candidate import STAGES
from scripts.s4_shared_transport_offline import SharedIntegrityError, SharedTransport
from scripts.s4_synthetic_cost_ledger import SyntheticBudgetExhausted, SyntheticCostLedger
from writscope.artifacts import digest
from writscope.providers import ModelConfig, ModelRequest
from writscope.providers.deepseek import wire_bytes


def mock_wire(preview):
    stage = preview.get("stage")
    if (preview.get("version") != "s4_prompt_contract_preview_v1" or preview.get("status") != "draft_offline_only" or
            stage not in STAGES or preview.get("paid_execution_allowed") is not False or
            preview.get("external_transmission_allowed") is not False or preview.get("model") is not None or
            digest(preview["instructions"].encode("utf-8")) != preview["prompt_sha256"]):
        raise ValueError("invalid offline mock request")
    request = ModelRequest(method="incremental_extract" if stage == "incremental_extract" else
                           "full_checklist" if stage == "full_checklist" else "full_direct",
                           output_contract="transition_quote_v1" if stage == "incremental_extract" else "source_quote_v1",
                           model_config_values=ModelConfig(model_id="deepseek-flash", temperature=0.0, max_output_tokens=4096),
                           instructions=preview["instructions"], prompt_sha256=preview["prompt_sha256"],
                           evidence_sha256=preview["evidence_sha256"], public_input=preview["public_input"],
                           output_schema=preview["output_schema"])
    return wire_bytes(request)


class SyntheticBudgetStop(SharedIntegrityError):
    """No mock dispatch occurred because the synthetic budget is exhausted."""


class SyntheticCostGate:
    """Compose existing fixed MockTransport with a separate synthetic budget.

    The two stores are not one transaction. Any interruption leaves the cost
    reservation unresolved and must block further work; not usable for real I/O.
    """
    def __init__(self, cost_ledger, transport):
        if (type(cost_ledger) is not SyntheticCostLedger or type(transport) is not SharedTransport or
                cost_ledger.plan_hash != transport.plan_hash or cost_ledger.policy["max_output_tokens"] != 4096):
            raise ValueError("only fixed offline mock components allowed")
        self.cost, self.transport = cost_ledger, transport

    def _finish(self, attempt, outcome):
        try:
            self.cost.finish(attempt, outcome)
        except Exception as exc:
            raise SharedIntegrityError("synthetic cost finish failed") from exc

    def dispatch(self, job_id, stage, request, step):
        try:
            if stage != request.get("stage"):
                raise ValueError("synthetic stage mismatch")
            attempt = self.cost.reserve(job_id, stage, mock_wire(request))
            self.cost.mark_dispatch(attempt)
        except SyntheticBudgetExhausted as exc:
            raise SyntheticBudgetStop("synthetic budget exhausted") from exc
        except Exception as exc:
            raise SharedIntegrityError("synthetic cost gate stopped: " + str(exc)) from exc
        try:
            self.transport.dispatch(job_id, stage, request, step)
        except ValueError:
            self._finish(attempt, "failed")
            raise
        # A journal integrity exception deliberately leaves an unresolved cost
        # reservation; no more calls may proceed automatically.
        self._finish(attempt, "failed" if step is None or step.action == "error" else "completed")
