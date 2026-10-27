"""Offline provenance and replay checks for the versioned context ledger."""

from pathlib import Path

import pytest

from scripts.m3i_task_context import (ContextBatch, ContextQuote, TaskContextLedger,
                                      replay_context)
from writscope.contracts import load_bundle


ROOT = Path(__file__).resolve().parents[1]


def _ledger(case="c001"):
    bundle = load_bundle(ROOT / "reports/m1-2026-10-18/inputs/verified_impact" / case)
    return TaskContextLedger(bundle.review_input.events, set(bundle.registry.artifacts))


def _batch(seq, event_id, *quotes):
    return ContextBatch(event_id=event_id, seq=seq,
                        items=[ContextQuote(source_quote=quote, verification_refs=[])
                               for quote in quotes])


@pytest.mark.parametrize("case", ["c001", "c002", "c003"])
def test_multiple_context_quotes_from_one_user_event_replay(case):
    ledger = _ledger(case)
    ledger.submit(_batch(1, "e001", "修复新页面的状态显示", "可以在客户端本地映射成页面需要的 state。"))
    ledger.submit(_batch(2, "e002"))
    ledger.submit(_batch(3, "e003"))
    ledger.submit(_batch(4, "e004", "尽量减少改动", "不增加接口或网络请求。"))
    ledger.submit(_batch(5, "e005"))
    state = ledger.snapshot()
    assert state.through_seq == 5
    assert [item.source_ref.event_id for item in state.task_context] == ["e001", "e001", "e004", "e004"]
    assert all(item.requirement == item.source_ref.quote for item in state.task_context)
    assert replay_context(ledger.events, ledger.records, ledger.artifact_ids) == state
    assert all(record.before_hash != record.after_hash for record in ledger.records)


def test_rejects_tool_text_claiming_user_authority_and_keeps_cursor():
    ledger = _ledger()
    ledger.submit(_batch(1, "e001"))
    with pytest.raises(ValueError, match="trusted user"):
        ledger.submit(_batch(2, "e002", "状态显示失败"))
    assert ledger.through_seq == 1 and len(ledger.records) == 1
    ledger.submit(_batch(2, "e002"))


def test_rejects_bad_source_references_and_atomic_batch():
    ledger = _ledger()
    with pytest.raises(ValueError, match="missing or non-unique"):
        ledger.submit(_batch(1, "e001", "修复新页面的状态显示", "不存在的要求"))
    assert ledger.snapshot().through_seq == 0 and not ledger.items
    with pytest.raises(ValueError, match="unknown public"):
        ledger.submit(ContextBatch(event_id="e001", seq=1,
                                   items=[ContextQuote(source_quote="修复新页面的状态显示",
                                                       verification_refs=["private-label"])]))
    assert ledger.snapshot().through_seq == 0


def test_rejects_duplicate_out_of_order_and_tampered_replay():
    ledger = _ledger()
    with pytest.raises(ValueError, match="duplicate task-context quote"):
        _batch(1, "e001", "修复新页面的状态显示", "修复新页面的状态显示")
    with pytest.raises(ValueError, match="out of order"):
        ledger.submit(_batch(2, "e002"))
    record = ledger.submit(_batch(1, "e001", "修复新页面的状态显示"))
    with pytest.raises(ValueError, match="out of order"):
        ledger.submit(record.batch)
    bad = record.model_copy(update={"after_hash": "0" * 64})
    with pytest.raises(ValueError, match="replay mismatch"):
        replay_context(ledger.events, [bad], ledger.artifact_ids)
