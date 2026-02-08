"""Schemas for quality check endpoint."""

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


class CheckRequest(BaseModel):
    """Request for quality check."""
    check_level: Literal["quick", "standard", "deep"] = "standard"
    max_visual_samples: int = Field(8, ge=1, le=20)
    resolution: str = "640x360"


class QualityScore(BaseModel):
    """Quality scores across dimensions."""
    structure: int = Field(0, ge=0, le=100)
    sync: int = Field(0, ge=0, le=100)
    completeness: int = Field(0, ge=0, le=100)
    visual: int = Field(0, ge=0, le=100)
    overall: int = Field(0, ge=0, le=100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        if self.overall >= 90:
            return "A"
        elif self.overall >= 75:
            return "B"
        elif self.overall >= 60:
            return "C"
        elif self.overall >= 40:
            return "D"
        return "F"


class RecommendedAction(BaseModel):
    """Recommended action to fix an issue."""
    action_type: Literal[
        "rerun_skill",
        "adjust_clip",
        "request_material",
        "manual_review",
    ]
    skill: str | None = None
    description: str


class CheckIssue(BaseModel):
    """A quality check issue."""
    severity: Literal["critical", "warning", "info"]
    category: str  # "structure", "sync", "completeness", "visual", "material"
    description: str
    time_ms: int | None = None
    recommended_action: RecommendedAction | None = None
    requires_user_input: bool = False


class MaterialRequirement(BaseModel):
    """A detected material/asset gap."""
    description: str
    suggestions: list[str] = Field(default_factory=list)


class CheckResponse(BaseModel):
    """Response from quality check."""
    scores: QualityScore
    issues: list[CheckIssue]
    material_requirements: list[MaterialRequirement] = Field(default_factory=list)
    pass_threshold_met: bool  # overall >= 70 && critical == 0
    visual_sampling_skipped: bool = False
    iteration_recommendation: Literal[
        "pass",
        "auto_fixable",
        "needs_user_input",
        "needs_manual_review",
    ]
