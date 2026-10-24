"""Per-task evidence contracts, independent from model serialization or execution."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .schemas import Contract, Digest

CapabilityState = Literal[
    "production_validated", "experimental_opt_in", "research_only", "disabled"
]
EVIDENCE_DIMENSIONS = (
    "recognition",
    "segmentation",
    "outline_fidelity",
    "writer_fidelity",
    "shaping",
    "human_preference",
    "correction_burden",
    "resources",
)


class CapabilityKey(Contract):
    task: str = Field(min_length=1, max_length=80)
    script: str = Field(pattern=r"^[A-Z][a-z]{3}$")
    style: Literal["print", "cursive", "mixed"]
    input_class: str = Field(min_length=1, max_length=80)


class Qualification(Contract):
    key: CapabilityKey
    state: CapabilityState = "research_only"
    required_dimensions: list[str] = Field(min_length=1)
    evidence: dict[str, Digest | None] = Field(default_factory=dict)
    heldout_real_writers: bool = False
    independent_approval: Digest | None = None
    regressed_dimensions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_gate(self) -> Self:
        if not set(self.required_dimensions) <= set(EVIDENCE_DIMENSIONS):
            raise ValueError("unknown_evidence_dimension")
        if not set(self.evidence) <= set(EVIDENCE_DIMENSIONS):
            raise ValueError("unknown_evidence_dimension")
        if self.state == "production_validated" and (
            not self.heldout_real_writers
            or self.independent_approval is None
            or self.regressed_dimensions
            or any(self.evidence.get(d) is None for d in self.required_dimensions)
        ):
            raise ValueError("independent_matching_evidence_required")
        return self


class SyntheticProposal(Contract):
    """Future interoperability only; no generator or automatic activation exists."""

    origin: Literal["geometric_reconstruction", "style_conditioned_generation"]
    state: Literal["disabled"] = "disabled"
    approval: Literal["pending"] = "pending"
    authentic_coverage: Literal[False] = False
    evidence: dict[str, None] = Field(default_factory=dict)
