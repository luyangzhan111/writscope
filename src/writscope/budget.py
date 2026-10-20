"""Conservative local reservation for a small real-model connection check.

This is a client-side stop rule, not a provider billing guarantee. A provider-
side project spending limit is still needed for a hard external ceiling.
"""

from decimal import Decimal

from pydantic import Field, model_validator

from .events import StrictModel


class PriceCeiling(StrictModel):
    input_cny_per_million: Decimal = Field(gt=0)
    output_cny_per_million: Decimal = Field(gt=0)
    source_url: str = Field(min_length=1)
    checked_at_utc: str = Field(min_length=1)
    # UTF-8 byte count is a deliberately high token proxy for the serialized
    # request, with another explicit allowance for provider framing overhead.
    framing_token_allowance: int = Field(default=8192, ge=0)


class RealBudgetPolicy(StrictModel):
    max_total_cny: Decimal = Field(gt=0)
    max_provider_attempts: int = Field(default=6, ge=1, le=12)
    max_attempts_per_job: int = Field(default=1, ge=1, le=3)
    timeout_seconds: float = Field(default=60, gt=0, le=180, allow_inf_nan=False)
    max_request_bytes: int = Field(default=1_000_000, ge=1)
    max_response_bytes: int = Field(default=100_000, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    concurrency: int = Field(default=1, ge=1, le=1)
    price_ceiling: PriceCeiling

    @model_validator(mode="after")
    def enough_attempts_for_six(self):
        if self.max_provider_attempts < 6:
            raise ValueError("M2b requires room for six planned jobs")
        return self


class ReservationLedger:
    def __init__(self, policy: RealBudgetPolicy):
        self.policy = policy
        self.reserved_cny = Decimal("0")
        self.attempts = 0

    def quote(self, wire_bytes: int) -> Decimal:
        if wire_bytes > self.policy.max_request_bytes:
            raise ValueError("wire request exceeds configured limit")
        price = self.policy.price_ceiling
        input_tokens_upper = wire_bytes + price.framing_token_allowance
        return ((Decimal(input_tokens_upper) * price.input_cny_per_million +
                 Decimal(self.policy.max_output_tokens) * price.output_cny_per_million) /
                Decimal(1_000_000))

    def reserve(self, wire_bytes: int) -> Decimal:
        quote = self.quote(wire_bytes)
        if self.attempts >= self.policy.max_provider_attempts or self.reserved_cny + quote > self.policy.max_total_cny:
            raise ValueError("local call/estimated-cost budget exhausted")
        self.attempts += 1
        self.reserved_cny += quote
        # Reservations are never released after timeout or missing usage.
        return quote
