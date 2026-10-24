"""Versioned research records. A validated record is not a validated ML capability."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,119}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Script = Literal["Latn", "Deva"]
Style = Literal["print", "cursive", "mixed"]
Split = Literal["train", "validation", "test", "quarantine"]
Use = Literal[
    "development_fixture", "research_evaluation", "research_training", "production_training"
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)
    schema_version: Literal[1] = 1


class Evidence(Record):
    """A source, not an inference that permission or consent has been obtained."""

    uri: Annotated[str, Field(min_length=1, max_length=1000)]
    content_hash: Digest | None = None
    checked_on: date
    finding: Annotated[str, Field(min_length=1, max_length=1500)]


class LicenseReview(Record):
    identifier: str | None = None
    status: Literal["pending", "verified", "not_applicable"]
    evidence: tuple[Evidence, ...] = ()
    permitted_uses: tuple[Use, ...] = ()
    restrictions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def permission_requires_evidence(self) -> Self:
        if self.status == "verified" and (not self.identifier or not self.evidence):
            raise ValueError("verified license requires identifier and evidence")
        if self.status == "pending" and self.permitted_uses:
            raise ValueError("unreviewed license cannot grant uses")
        return self


class ConsentReview(Record):
    status: Literal["pending", "not_applicable", "separate_opt_in", "withdrawn"]
    policy_version: str | None = None
    consent_ref: Identifier | None = None
    evidence: tuple[Evidence, ...] = ()
    withdrawal_exclusion: Literal[True] = True

    @model_validator(mode="after")
    def independent_consent(self) -> Self:
        if self.status == "separate_opt_in" and (
            not self.policy_version or not self.consent_ref or not self.evidence
        ):
            raise ValueError("opt-in requires independent versioned consent evidence")
        return self


class ResearchSample(Record):
    sample_id: Identifier
    writer_id: Identifier
    # Global registry group, shared across datasets and all font-family variants.
    writer_group_id: Identifier | None
    script_code: Script
    style: Style
    split: Split
    content_hash: Digest
    # Every augmentation/crop must retain its ancestor group and source hashes.
    source_group_id: Identifier
    duplicate_group_id: Identifier | None = None
    parent_content_hashes: tuple[Digest, ...] = ()
    consent_ref: Identifier | None = None
    consent_state: Literal["not_applicable", "active", "withdrawn", "pending"]

    @model_validator(mode="after")
    def eligible_sample(self) -> Self:
        if self.split != "quarantine":
            if not self.writer_group_id:
                raise ValueError("unresolved writer must remain in quarantine")
            if self.consent_state in {"withdrawn", "pending"}:
                raise ValueError("withdrawn/unresolved contribution must remain in quarantine")
            if self.consent_state == "active" and not self.consent_ref:
                raise ValueError("active contribution requires consent reference")
        return self


class DatasetManifest(Record):
    record_type: Literal["dataset_manifest"] = "dataset_manifest"
    dataset_id: Identifier
    version: Identifier
    status: Literal["candidate", "planned", "metadata_fixture", "approved"]
    source_kind: Literal[
        "licensed_public", "synthetic_font", "curated", "opt_in", "metadata_fixture"
    ]
    source: tuple[Evidence, ...] = Field(min_length=1)
    script_codes: tuple[Script, ...] = Field(min_length=1)
    style_partitions: tuple[Style, ...] = Field(min_length=1)
    license: LicenseReview
    consent: ConsentReview
    writer_resolution: Literal["pending", "resolved"]
    writer_registry_ref: str | None
    raw_data_in_repository: Literal[False] = False
    content_hash: Digest | None = None
    samples: tuple[ResearchSample, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def dataset_governance(self) -> Self:
        if len({sample.sample_id for sample in self.samples}) != len(self.samples):
            raise ValueError("duplicate sample id")
        if (self.status == "metadata_fixture") != (self.source_kind == "metadata_fixture"):
            raise ValueError("metadata fixtures cannot become approved handwriting datasets")
        if self.status == "metadata_fixture":
            if self.license.status != "not_applicable" or self.consent.status != "not_applicable":
                raise ValueError("metadata fixtures cannot assert human data permissions")
        elif self.license.status == "not_applicable":
            raise ValueError("handwriting/font data requires license review")
        if self.source_kind in {"curated", "opt_in"}:
            for sample in self.samples:
                if sample.split != "quarantine" and (
                    self.consent.status != "separate_opt_in"
                    or sample.consent_state != "active"
                    or sample.consent_ref != self.consent.consent_ref
                ):
                    raise ValueError("private training data requires separate active consent")
        for sample in self.samples:
            if (
                sample.script_code not in self.script_codes
                or sample.style not in self.style_partitions
            ):
                raise ValueError("sample missing its explicit script/style partition")
        if self.status == "approved":
            if (
                self.license.status != "verified"
                or self.consent.status in {"pending", "withdrawn"}
                or self.writer_resolution != "resolved"
                or not self.writer_registry_ref
                or not self.samples
                or not self.content_hash
            ):
                raise ValueError(
                    "approved dataset requires license, consent, writers and pinned content"
                )
            if self.source_kind == "licensed_public" and not self.consent.evidence:
                raise ValueError("public dataset consent/provenance review requires evidence")
        return self


class ArtifactRef(Record):
    record_id: Identifier
    content_hash: Digest


class WriterIdentity(Record):
    dataset_id: Identifier
    writer_id: Identifier
    writer_group_id: Identifier
    evidence: str


class WriterRegistry(Record):
    record_type: Literal["writer_registry"] = "writer_registry"
    registry_id: Identifier
    version: Identifier
    identities: tuple[WriterIdentity, ...]
    policy: Literal["global_pseudonymous_groups_no_identity_inference"] = (
        "global_pseudonymous_groups_no_identity_inference"
    )
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identity_mapping(self) -> Self:
        identities = {(item.dataset_id, item.writer_id) for item in self.identities}
        if len(identities) != len(self.identities):
            raise ValueError("writer identity must resolve to exactly one global group")
        return self


class Metric(Record):
    name: Identifier
    unit: Identifier
    value: Finite | None = None
    n: Annotated[int, Field(gt=0)] | None = None
    confidence_interval: tuple[Finite, Finite] | None = None

    @model_validator(mode="after")
    def measured_metric(self) -> Self:
        if self.value is None and (self.n is not None or self.confidence_interval is not None):
            raise ValueError("unmeasured metric cannot have sample size or confidence interval")
        if self.value is not None and self.n is None:
            raise ValueError("measured metric requires sample size")
        if (
            self.confidence_interval
            and self.value is not None
            and not (self.confidence_interval[0] <= self.value <= self.confidence_interval[1])
        ):
            raise ValueError("confidence interval must contain measured value")
        return self


class BenchmarkRecord(Record):
    record_type: Literal["benchmark_record"] = "benchmark_record"
    record_id: Identifier
    created_on: date
    status: Literal["unmeasured", "measured"]
    measured_on: date | None = None
    dimension: Literal[
        "recognition",
        "alignment_segmentation",
        "outline_quality",
        "writer_fidelity",
        "cursive_behavior",
        "script_shaping",
        "font_export",
        "user_burden",
        "operations",
    ]
    script_code: Script
    style: Style
    stage: Identifier
    input_class: Identifier
    condition: Literal["clean", "adverse"] = "clean"
    evidence_domain: Literal["real_heldout", "synthetic_control"] = "real_heldout"
    split: Literal["validation", "test"]
    dataset_manifests: tuple[ArtifactRef, ...] = ()
    split_registry_hash: Digest | None = None
    model_ref: ArtifactRef | None = None
    config_hash: Digest | None = None
    code_ref: str | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)
    provider: str | None = None
    runtime: str | None = None
    cpu: str | None = None
    ram_mb: Nonnegative | None = None
    n: Annotated[int, Field(gt=0)] | None = None
    p50_ms: Nonnegative | None = None
    p95_ms: Nonnegative | None = None
    peak_rss_mb: Nonnegative | None = None
    bytes_in: Annotated[int, Field(ge=0)] | None = None
    bytes_out: Annotated[int, Field(ge=0)] | None = None
    metrics: tuple[Metric, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def no_fabricated_measurements(self) -> Self:
        measured_fields = (
            self.measured_on,
            self.n,
            self.p50_ms,
            self.p95_ms,
            self.peak_rss_mb,
            self.bytes_in,
            self.bytes_out,
            self.ram_mb,
        )
        if self.status == "unmeasured" and (
            any(value is not None for value in measured_fields)
            or any(metric.value is not None for metric in self.metrics)
        ):
            raise ValueError("unmeasured benchmark must retain null results")
        if self.status == "measured" and (
            self.measured_on is None
            or self.n is None
            or not self.dataset_manifests
            or not self.split_registry_hash
            or not self.config_hash
            or not self.code_ref
            or not self.tool_versions
            or not self.runtime
            or not self.cpu
            or not any(metric.value is not None for metric in self.metrics)
        ):
            raise ValueError(
                "measured benchmark requires reproducible provenance and observed metrics"
            )
        if self.p50_ms is not None and self.p95_ms is not None and self.p95_ms < self.p50_ms:
            raise ValueError("p95 must not be less than p50")
        return self


Stage = Literal[
    "dataset_research",
    "baseline_comparison",
    "training",
    "heldout_evaluation",
    "model_registration",
    "staged_deployment",
    "monitoring",
]


class ExperimentRecord(Record):
    record_type: Literal["experiment_record"] = "experiment_record"
    record_id: Identifier
    created_on: date
    stage: Stage
    status: Literal["planned", "completed"]
    script_code: Script
    style: Style
    method_kind: Literal["classical_cv", "pretrained", "fine_tune", "custom_training"] | None = None
    compared_methods: tuple[ArtifactRef, ...] = ()
    selected_model_ref: ArtifactRef | None = None
    training_required: bool | None = None
    selection_rationale: str | None = None
    recipe_ref: ArtifactRef | None = None
    datasets: tuple[ArtifactRef, ...] = ()
    parents: tuple[ArtifactRef, ...] = ()
    benchmarks: tuple[ArtifactRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    code_ref: str | None = None
    environment_lock_hash: Digest | None = None
    config_hash: Digest | None = None
    seed: int | None = None
    command: tuple[str, ...] = ()
    capability_status: Literal["research_only"] = "research_only"
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reproducible_completed_work(self) -> Self:
        if self.status == "completed" and (
            not self.code_ref
            or not self.environment_lock_hash
            or not self.config_hash
            or not self.command
            or self.seed is None
            or not self.artifacts
        ):
            raise ValueError(
                "completed research requires pinned code, environment, config, seed and outputs"
            )
        if (
            self.status == "completed"
            and self.stage == "training"
            and (
                not self.selection_rationale
                or not self.recipe_ref
                or not self.training_required
                or not self.datasets
                or self.method_kind not in {"fine_tune", "custom_training"}
            )
        ):
            raise ValueError("training requires evidence-based justification and pinned recipe")
        return self


class ShapingCase(Record):
    case_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=500)]
    codepoints: tuple[Annotated[int, Field(ge=0, le=0x10FFFF)], ...]
    behavior: str
    features_to_inspect: tuple[str, ...]
    # Expected glyph IDs/positions depend on the particular approved font.
    expected_output_hash: Digest | None = None

    @model_validator(mode="after")
    def preserve_unicode_evidence(self) -> Self:
        if any(0xD800 <= point <= 0xDFFF for point in self.codepoints):
            raise ValueError("surrogate is not a Unicode scalar")
        if tuple(map(ord, self.text)) != self.codepoints:
            raise ValueError("shaping text and codepoints differ; normalization is explicit")
        return self


class ShapingCorpus(Record):
    record_type: Literal["shaping_corpus"] = "shaping_corpus"
    corpus_id: Identifier
    version: Identifier
    script_code: Literal["Deva"] = "Deva"
    status: Literal["starter_not_evaluated"] = "starter_not_evaluated"
    source: tuple[Evidence, ...] = Field(min_length=1)
    font_ref: ArtifactRef | None = None
    harfbuzz_version: str | None = None
    normalization_policy: Literal["preserve_input_codepoints"] = "preserve_input_codepoints"
    common_word_inventory: Literal[False] = False
    cases: tuple[ShapingCase, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)
