"""Owner-reviewed Indic geometry evidence; feature text is never accepted."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .schemas import Codepoints, Contract, Digest, Point

Feature = Literal[
    "ccmp",
    "nukt",
    "akhn",
    "rphf",
    "rkrf",
    "blwf",
    "half",
    "cjct",
    "pres",
    "abvs",
    "blws",
    "psts",
    "haln",
]


class Substitution(Contract):
    feature: Feature
    input_codepoints: Codepoints


class IndicEvidence(Contract):
    schema_version: Literal[1] = 1
    role: Literal["base", "mark", "half", "reph", "rakar", "conjunct", "contextual"]
    source_classification: Literal[
        "authentic_capture", "owner_engineering_record", "engineering_fixture"
    ]
    geometry_hash: Digest
    anchors: dict[
        Literal[
            "above_base",
            "below_base",
            "above_mark",
            "below_mark",
            "stack_above_base",
            "stack_above_mark",
        ],
        Point,
    ] = Field(default_factory=dict, max_length=6)
    substitution: Substitution | None = None

    @model_validator(mode="after")
    def context(self) -> Self:
        if any(abs(p.x) > 16000 or abs(p.y) > 16000 for p in self.anchors.values()):
            raise ValueError("anchor_out_of_policy")
        if self.substitution:
            cps = self.substitution.input_codepoints
            feature = self.substitution.feature
            valid = (
                feature == "rphf"
                and self.role == "reph"
                and cps == [0x930, 0x94D]
                or feature == "half"
                and self.role == "half"
                and len(cps) == 2
                and cps[-1] == 0x94D
                or feature == "rkrf"
                and self.role == "rakar"
                and len(cps) >= 3
                and cps[-2:] == [0x94D, 0x930]
                or feature in {"akhn", "cjct"}
                and self.role == "conjunct"
                and 0x94D in cps
                or feature in {"nukt", "ccmp"}
                and self.role in {"base", "contextual"}
                and 0x93C in cps
                or feature in {"blwf", "pres", "abvs", "blws", "psts", "haln"}
                and self.role == "contextual"
            )
            if not valid:
                raise ValueError("unsupported_indic_feature_context")
        return self
