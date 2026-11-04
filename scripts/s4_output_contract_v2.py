"""S4 wrapped outputs v2: expose existing kernel shapes, never repair responses.

Operation classes reuse the S3 shape contract; the state, source, scope,
conditions, lifecycle and exact-value kernels are unchanged.
"""
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter, model_validator

from scripts import s3_interaction_v2 as shapes
from scripts import s4_prompt_candidate as legacy
from scripts.s4_value_scope import ExactValueScope
from writscope.contracts import Checklist, QuoteReviewPayload
from writscope.events import StrictModel

VERSION = 's4_wrapped_operation_shapes_v2'
PROMPTS = Path(__file__).resolve().parents[1]/'prompts/s4_candidate_v2'
STAGES = legacy.STAGES


class AddConstraint(shapes.AddConstraint):
    value_scope: None


class Grant(shapes.Grant):
    value_scope: ExactValueScope | None

    @model_validator(mode='after')
    def value_binding(self):
        if self.value_scope is not None:
            auth, scope = self.authorization, self.value_scope
            if (auth.change_type != 'change_value_semantics' or len(auth.subjects) != 1 or
                    auth.subjects[0] != scope.subject or scope.subject.field != auth.change_details.old_field or
                    scope.old_value not in self.source_quote or scope.new_value not in self.source_quote):
                raise ValueError('value scope must bind to this exact semantic grant and current quote')
        return self


class ReplaceConstraint(shapes.ReplaceConstraint):
    value_scope: None


class ReplaceAuthorization(shapes.ReplaceAuthorization):
    value_scope: ExactValueScope | None

    @model_validator(mode='after')
    def value_binding(self):
        return Grant.value_binding(self)


class Revoke(shapes.Revoke):
    value_scope: None


class MarkAmbiguous(shapes.MarkAmbiguous):
    value_scope: None


class NoChange(shapes.NoChange):
    value_scope: None


Mutation = Union[AddConstraint, Grant, ReplaceConstraint, ReplaceAuthorization, Revoke, MarkAmbiguous]
NoChangeItems = Annotated[list[NoChange], Field(min_length=1, max_length=1)]
MutationItems = Annotated[list[Mutation], Field(min_length=1, max_length=8)]


class Batch(StrictModel):
    kind: Literal['batch']
    items: Union[NoChangeItems, MutationItems]


class ChecklistPayload(QuoteReviewPayload):
    checklist: Checklist


class Final(StrictModel):
    kind: Literal['final']
    payload: QuoteReviewPayload


class ChecklistFinal(Final):
    payload: ChecklistPayload


def adapter(stage, allow_lookup):
    if stage not in STAGES or type(allow_lookup) is not bool:
        raise ValueError('invalid v2 stage/lookup')
    primary = Batch if stage == 'incremental_extract' else ChecklistFinal if stage == 'full_checklist' else Final
    return TypeAdapter(Annotated[Union[primary, legacy.Lookup], Field(discriminator='kind')] if allow_lookup else primary)


def prompt(stage):
    if stage not in STAGES:
        raise ValueError('invalid v2 stage')
    # Append only generic contract guidance. Original method instructions remain.
    return legacy.prompt(stage)+(PROMPTS/'common.md').read_text(encoding='utf8').strip()+'\n\n'+(
        PROMPTS/(stage+'.md')).read_text(encoding='utf8').strip()+'\n'


def preview(*args, **kwargs):
    value = legacy.preview(*args, **kwargs)
    instructions = prompt(value['stage'])
    from writscope.artifacts import digest
    value.update(instructions=instructions, prompt_sha256=digest(instructions.encode()),
                 output_schema=adapter(value['stage'], value['lookup_allowed']).json_schema())
    # Keep the internal preview transport format; these offline flags are not
    # sent in the API body. Actual execution permission belongs to the launcher.
    return value
