"""Quality Checker — orchestrates all quality sub-checks.

Combines: CompositionValidator, PlanActualDiffService,
SemanticCheckService, DirectorsEyeService into a single check result.
"""

import logging
from typing import Any

from src.schemas.quality_check import (
    CheckIssue,
    CheckRequest,
    CheckResponse,
    MaterialRequirement,
    QualityScore,
    RecommendedAction,
)
from src.services.composition_validator import CompositionValidator
from src.services.directors_eye_service import DirectorsEyeService
from src.services.plan_actual_diff_service import PlanActualDiffService
from src.services.semantic_check_service import SemanticCheckService

logger = logging.getLogger(__name__)

# Mapping from validation rules to recommended skill re-runs
RULE_ACTION_MAP: dict[str, dict[str, Any]] = {
    "missing_assets": {
        "action_type": "request_material",
        "description": "Upload the missing asset",
    },
    "overlapping_clips": {
        "action_type": "rerun_skill",
        "skill": "layout",
        "description": "Re-run layout to fix clip overlaps",
    },
    "audio_sync": {
        "action_type": "rerun_skill",
        "skill": "sync-content",
        "description": "Re-run sync-content to fix audio-visual alignment",
    },
    "gap_detection": {
        "action_type": "rerun_skill",
        "skill": "sync-content",
        "description": "Re-run sync-content to fill visual gaps",
    },
    "text_readability": {
        "action_type": "rerun_skill",
        "skill": "add-telop",
        "description": "Re-run add-telop to fix text display issues",
    },
    "safe_zone": {
        "action_type": "rerun_skill",
        "skill": "layout",
        "description": "Re-run layout to fix safe zone violations",
    },
}


class QualityChecker:
    """Orchestrates quality checks and produces a unified result."""

    # Score weights
    WEIGHTS = {
        "structure": 0.25,
        "sync": 0.30,
        "completeness": 0.25,
        "visual": 0.20,
    }

    def __init__(
        self,
        timeline_data: dict[str, Any],
        video_plan: dict[str, Any] | None = None,
        asset_ids: set[str] | None = None,
        asset_name_map: dict[str, str] | None = None,
        project_width: int = 1920,
        project_height: int = 1080,
        visual_sample_results: list[dict[str, Any]] | None = None,
    ):
        self.timeline = timeline_data
        self.video_plan = video_plan
        self.asset_ids = asset_ids or set()
        self.asset_name_map = asset_name_map or {}
        self.project_width = project_width
        self.project_height = project_height
        self.visual_samples = visual_sample_results or []

    def run(self, request: CheckRequest) -> CheckResponse:
        """Run quality check at the specified level."""
        issues: list[CheckIssue] = []
        materials: list[MaterialRequirement] = []

        # Always run structure check
        structure_score = self._check_structure(issues)

        # Completeness (plan vs actual)
        completeness_score = self._check_completeness(issues, materials)

        # Sync check (standard and deep only)
        sync_score = 100
        if request.check_level in ("standard", "deep"):
            sync_score = self._check_sync(issues, materials)

        # Visual score (from sample frames, standard and deep)
        visual_score = 100
        if request.check_level in ("standard", "deep") and self.visual_samples:
            visual_score = self._check_visual(issues)

        # Director's Eye (deep only)
        if request.check_level == "deep":
            self._check_directors_eye(issues, materials)

        # Calculate overall score
        overall = int(
            structure_score * self.WEIGHTS["structure"]
            + sync_score * self.WEIGHTS["sync"]
            + completeness_score * self.WEIGHTS["completeness"]
            + visual_score * self.WEIGHTS["visual"]
        )

        scores = QualityScore(
            structure=structure_score,
            sync=sync_score,
            completeness=completeness_score,
            visual=visual_score,
            overall=overall,
        )

        # Determine pass/fail
        critical_count = sum(1 for i in issues if i.severity == "critical")
        pass_met = overall >= 70 and critical_count == 0

        # Determine iteration recommendation
        recommendation = self._determine_recommendation(issues, materials)

        return CheckResponse(
            scores=scores,
            issues=issues,
            material_requirements=materials,
            pass_threshold_met=pass_met,
            iteration_recommendation=recommendation,
        )

    def _check_structure(self, issues: list[CheckIssue]) -> int:
        """Run composition validation and convert to score."""
        validator = CompositionValidator(
            timeline_data=self.timeline,
            project_width=self.project_width,
            project_height=self.project_height,
            asset_ids=self.asset_ids,
        )

        validation_issues = validator.validate()

        error_count = 0
        warning_count = 0

        for vi in validation_issues:
            severity: str
            if vi.severity == "error":
                severity = "critical"
                error_count += 1
            elif vi.severity == "warning":
                severity = "warning"
                warning_count += 1
            else:
                severity = "info"

            # Map to recommended action
            action = RULE_ACTION_MAP.get(vi.rule)
            rec = None
            if action:
                rec = RecommendedAction(
                    action_type=action["action_type"],
                    skill=action.get("skill"),
                    description=action["description"],
                )

            issues.append(CheckIssue(
                severity=severity,
                category="structure",
                description=f"[{vi.rule}] {vi.message}" + (f" — {vi.suggestion}" if vi.suggestion else ""),
                time_ms=vi.time_ms,
                recommended_action=rec,
                requires_user_input=(action or {}).get("action_type") == "request_material",
            ))

        # Score: 100 - (errors * 15) - (warnings * 5), clamped to [0, 100]
        score = max(0, min(100, 100 - (error_count * 15) - (warning_count * 5)))
        return score

    def _check_completeness(
        self,
        issues: list[CheckIssue],
        materials: list[MaterialRequirement],
    ) -> int:
        """Check plan vs actual completeness."""
        diff_svc = PlanActualDiffService(
            video_plan=self.video_plan,
            timeline_data=self.timeline,
        )
        diff = diff_svc.compare()

        # Add issues for missing elements
        for missing in diff.missing_elements:
            issues.append(CheckIssue(
                severity="warning",
                category="completeness",
                description=missing.get("description", "Missing planned element"),
                time_ms=None,
                recommended_action=RecommendedAction(
                    action_type="rerun_skill",
                    skill="sync-content",
                    description="Re-run sync-content or re-apply plan",
                ),
            ))

        # Add issues for timing drift
        for drift in diff.timing_drift_ms:
            if drift["drift_ms"] > 5000:
                issues.append(CheckIssue(
                    severity="warning",
                    category="completeness",
                    description=(
                        f"Timing drift: element '{drift['element_id']}' on {drift['layer']} "
                        f"drifted {drift['drift_ms']}ms from plan"
                    ),
                    time_ms=drift["actual_start_ms"],
                ))

        return int(diff.match_percentage)

    def _check_sync(
        self,
        issues: list[CheckIssue],
        materials: list[MaterialRequirement],
    ) -> int:
        """Check narration-content sync."""
        sem_svc = SemanticCheckService(
            timeline_data=self.timeline,
            asset_name_map=self.asset_name_map,
        )
        sem_result = sem_svc.check()

        # Add issues for unmatched segments
        for detail in sem_result.details:
            if detail.match_status == "no_content":
                issues.append(CheckIssue(
                    severity="warning",
                    category="sync",
                    description=f"Narration at {detail.timeline_start_ms}ms has no visual content",
                    time_ms=detail.timeline_start_ms,
                    recommended_action=RecommendedAction(
                        action_type="request_material",
                        description="Add visual content for this narration segment",
                    ),
                    requires_user_input=True,
                ))
                materials.append(MaterialRequirement(
                    description=(
                        f"Narration at {detail.timeline_start_ms}ms says "
                        f"'{detail.segment_text[:50]}' but no visual content is displayed"
                    ),
                    suggestions=[
                        "Upload a relevant slide or screen capture",
                        "Add visual content to the content layer",
                    ],
                ))

        return int(sem_result.match_rate)

    def _check_visual(self, issues: list[CheckIssue]) -> int:
        """Score visual quality from sample frames."""
        if not self.visual_samples:
            return 100

        valid_frames = 0
        total_frames = len(self.visual_samples)

        for sample in self.visual_samples:
            size_bytes = sample.get("size_bytes", 0)
            # A non-black frame should be > 1KB
            if size_bytes > 1024:
                valid_frames += 1
            else:
                issues.append(CheckIssue(
                    severity="warning",
                    category="visual",
                    description=f"Frame at {sample.get('time_ms', 0)}ms appears black/empty ({size_bytes} bytes)",
                    time_ms=sample.get("time_ms"),
                    recommended_action=RecommendedAction(
                        action_type="manual_review",
                        description="Visually inspect this frame to verify content is displayed",
                    ),
                ))

        return int(valid_frames / total_frames * 100) if total_frames > 0 else 100

    def _check_directors_eye(
        self,
        issues: list[CheckIssue],
        materials: list[MaterialRequirement],
    ) -> None:
        """Run Director's Eye analysis (deep level only)."""
        de_svc = DirectorsEyeService(
            video_plan=self.video_plan,
            timeline_data=self.timeline,
            asset_name_map=self.asset_name_map,
        )
        de_result = de_svc.analyze()

        for gap in de_result.gaps:
            issues.append(CheckIssue(
                severity="warning",
                category="material",
                description=gap.description,
                time_ms=gap.time_ms,
                recommended_action=RecommendedAction(
                    action_type="request_material",
                    description=gap.suggestions[0] if gap.suggestions else "Add missing content",
                ),
                requires_user_input=True,
            ))
            materials.append(MaterialRequirement(
                description=gap.description,
                suggestions=gap.suggestions,
            ))

        # BGM coverage check
        if de_result.bgm_coverage_percent < 50:
            issues.append(CheckIssue(
                severity="info",
                category="material",
                description=f"BGM covers only {de_result.bgm_coverage_percent:.0f}% of timeline",
                recommended_action=RecommendedAction(
                    action_type="request_material",
                    description="Add BGM track to cover the full timeline",
                ),
            ))

        # Transitions check
        if not de_result.has_section_transitions:
            issues.append(CheckIssue(
                severity="info",
                category="material",
                description="No transitions detected between sections",
                recommended_action=RecommendedAction(
                    action_type="request_material",
                    description="Add SE or effects at section boundaries",
                ),
            ))

    def _determine_recommendation(
        self,
        issues: list[CheckIssue],
        materials: list[MaterialRequirement],
    ) -> str:
        """Determine the iteration recommendation."""
        critical_issues = [i for i in issues if i.severity == "critical"]
        user_input_issues = [i for i in issues if i.requires_user_input]
        auto_fixable = [
            i for i in issues
            if i.recommended_action
            and i.recommended_action.action_type == "rerun_skill"
            and i.severity != "info"
        ]
        manual_review = [
            i for i in issues
            if i.recommended_action
            and i.recommended_action.action_type == "manual_review"
        ]

        if not critical_issues and not auto_fixable and not user_input_issues and not manual_review:
            return "pass"
        if user_input_issues or materials:
            return "needs_user_input"
        if manual_review:
            return "needs_manual_review"
        if auto_fixable or critical_issues:
            return "auto_fixable"
        return "pass"
