"""Pure accounting arithmetic; provenance and prices are the caller's responsibility."""
from decimal import Decimal, InvalidOperation

FIELDS = ("input_tokens", "output_tokens", "cache_hit_input_tokens", "cache_miss_input_tokens",
          "reasoning_tokens", "estimated_cost_cny", "billed_cost_cny", "elapsed_ms")
STAGES = ("extract", "lookup_continuation", "final", "local_update", "local_validation")


def aggregate(entries):
    """Count known values including zero; incomplete totals remain unknown."""
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    ids = [e["entry_id"] for e in entries]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("duplicate or invalid entry identity")
    if any(e["stage"] not in STAGES for e in entries):
        raise ValueError("unknown accounting stage")
    def totals(selected):
        out = {}
        for field in FIELDS:
            # Local computation has no provider token or provider bill; do not
            # treat non-applicable fields as missing provider measurements.
            relevant = selected if field == "elapsed_ms" else [e for e in selected if e["stage"] in {"extract", "lookup_continuation", "final"}]
            values = [e.get(field) for e in relevant]
            known = []
            for value in values:
                if value is None:
                    continue
                if isinstance(value, bool):
                    raise ValueError("invalid accounting value")
                try:
                    number = Decimal(str(value))
                except (InvalidOperation, ValueError):
                    raise ValueError("invalid accounting value") from None
                if not number.is_finite() or number < 0:
                    raise ValueError("invalid accounting value")
                if field.endswith("tokens") and number != number.to_integral_value():
                    raise ValueError("token count must be integral")
                known.append(number)
            subtotal = sum(known, Decimal(0))
            out[field] = {"known_sum": str(subtotal) if known else None, "known_count": len(known),
                          "unknown_count": len(values) - len(known),
                          "total": str(subtotal) if values and len(known) == len(values) else None}
        return out
    return {"entries": len(entries), "overall": totals(entries),
            "by_stage": {stage: totals([e for e in entries if e["stage"] == stage]) for stage in STAGES}}
