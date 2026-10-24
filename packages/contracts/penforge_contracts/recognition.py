"""Future Devanagari recognition boundary; no implementation or active model."""

from typing import Literal, Protocol

from pydantic import Field

from .schemas import Codepoints, Contract, Digest


class RecognitionHypothesis(Contract):
    source_hash: Digest
    script_code: Literal["Deva"] = "Deva"
    style: Literal["print", "cursive", "mixed"]
    codepoints: Codepoints
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    activates_identity: Literal[False] = False
    requires_owner_review: Literal[True] = True
    capability: Literal["research_only"] = "research_only"


class Recognizer(Protocol):
    def hypotheses(self, source: bytes, *, style: str) -> list[RecognitionHypothesis]: ...


DEVA_CAPABILITIES = {
    "standards_based_shaping_engineering": "experimental_opt_in",
    "structured_extraction": "experimental_opt_in",
    "recognition": "research_only",
    "freeform_cursive_segmentation": "research_only",
    "writer_fidelity": "unmeasured",
    "corpus_derived_tier2_coverage": "unavailable",
    "production_validated": False,
    "m3_complete": False,
}
