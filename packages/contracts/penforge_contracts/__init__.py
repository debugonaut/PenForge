"""Shared contracts only: no FastAPI, database, storage or ML dependencies."""

from .schemas import (
    CONTRACT_MODELS,
    AnalysisPayload,
    AnalyzeSamplesJob,
    AnalyzeSamplesJobV2,
    AnalyzeSamplesJobV3,
    AssetVerifyJob,
    CalibrationDefinition,
    CaptureAssignment,
    CaptureToFont,
    CompileExportJob,
    ContractRegistry,
    GlyphOutline,
    ImageRegionAssignment,
    JobDTO,
    MLResultManifest,
    ModelRegistryEntry,
    ProjectDTO,
    StrokeDocument,
    TemplateManifest,
    contract_registry,
    parse_analysis_payload,
    register_contract_schemas,
)
from .serialization import canonical_bytes, content_hash

__all__ = [
    "FontBuildManifest",
    "FontOptions",
    "safe_name",
    "CONTRACT_MODELS",
    "AnalyzeSamplesJob",
    "AnalyzeSamplesJobV2",
    "AnalyzeSamplesJobV3",
    "AnalysisPayload",
    "parse_analysis_payload",
    "AssetVerifyJob",
    "CalibrationDefinition",
    "CaptureAssignment",
    "CaptureToFont",
    "ImageRegionAssignment",
    "CompileExportJob",
    "ContractRegistry",
    "GlyphOutline",
    "JobDTO",
    "MLResultManifest",
    "ModelRegistryEntry",
    "ProjectDTO",
    "StrokeDocument",
    "TemplateManifest",
    "canonical_bytes",
    "content_hash",
    "contract_registry",
    "register_contract_schemas",
]

from .font_build import FontBuildManifest, FontOptions, safe_name

CONTRACT_MODELS["font_build_manifest"] = FontBuildManifest

from .privacy import PrivacyMaintenanceJob  # noqa: E402

__all__.append("PrivacyMaintenanceJob")
CONTRACT_MODELS["privacy_maintenance_job"] = PrivacyMaintenanceJob

from .freeform import FreeformAlignment, FreeformAnalysisInput  # noqa: E402

__all__.extend(["FreeformAlignment", "FreeformAnalysisInput"])
CONTRACT_MODELS["freeform_analysis_input"] = FreeformAnalysisInput
CONTRACT_MODELS["freeform_alignment"] = FreeformAlignment
