from decimal import Decimal

import pytest

from writscope.budget import PriceCeiling, RealBudgetPolicy, ReservationLedger
from writscope.m2b import policy as pilot_policy


def policy(max_total="1", attempts=6):
    return RealBudgetPolicy(max_total_cny=Decimal(max_total), max_provider_attempts=attempts,
                            price_ceiling=PriceCeiling(input_cny_per_million=Decimal("10"),
                                                       output_cny_per_million=Decimal("30"),
                                                       source_url="https://example.invalid/pricing",
                                                       checked_at_utc="2026-10-20T00:00:00Z"))


def test_reservations_count_failures_and_stop_before_overrun():
    ledger = ReservationLedger(policy(max_total="0.5"))
    quote = ledger.quote(10_000)
    assert quote == Decimal("0.3048")
    ledger.reserve(10_000)
    with pytest.raises(ValueError, match="budget exhausted"):
        ledger.reserve(10_000)
    assert ledger.attempts == 1 and ledger.reserved_cny == quote


def test_attempt_limit_and_wire_limit():
    ledger = ReservationLedger(policy(max_total="10"))
    with pytest.raises(ValueError, match="wire request"):
        ledger.reserve(1_000_001)
    for _ in range(6):
        ledger.reserve(0)
    with pytest.raises(ValueError, match="budget exhausted"):
        ledger.reserve(0)


def test_pilot_never_accepts_budget_above_user_cap():
    with pytest.raises(ValueError, match="at most 20 CNY"):
        pilot_policy(Decimal("20.01"))
