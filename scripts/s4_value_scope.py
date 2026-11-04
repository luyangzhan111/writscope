"""A narrow S4 exact-value matcher; not an authorization extractor or state engine."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from writscope.contracts import Subject


class ExactValueScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: Subject
    change_type: Literal["change_value_semantics"]
    old_value: str = Field(min_length=1)
    new_value: str = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self):
        if self.old_value == self.new_value:
            raise ValueError("value scope needs a concrete change")
        return self


def match_exact_value_scope(authorization, impact):
    """Compare trusted, already-extracted structures; omitted details stay ambiguous.

    This function does not establish user authority, lifecycle, constraints,
    conditions, source provenance or natural-language meaning. Those must be
    independently established before its result can support a final judgment.
    """
    try:
        grant = ExactValueScope.model_validate(authorization)
        effect = ExactValueScope.model_validate(impact)
    except (ValueError, TypeError):
        return "ambiguous"
    return "covered" if grant == effect else "not_covered"
