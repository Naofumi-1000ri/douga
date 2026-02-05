#!/usr/bin/env python3
"""Spec-only contract test runner for Douga AI-friendly API.

Validates x-constraints against fixture-based cases without calling the API.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OPENAPI_PATH = os.path.join(ROOT, "docs", "openapi", "douga-ai-friendly.yaml")
CASES_DIR = os.path.join(ROOT, "tests", "contract", "cases")
FIXTURES_DIR = os.path.join(ROOT, "tests", "contract", "fixtures")

PLACEHOLDERS = {
    "P": "11111111-1111-4111-8111-111111111111",
    "A": "22222222-2222-4222-8222-222222222222",
    "B": "33333333-3333-4333-8333-333333333333",
    "L1": "44444444-4444-4444-8444-444444444444",
    "T1": "55555555-5555-4555-8555-555555555555",
    "C1": "66666666-6666-4666-8666-666666666666",
    "AC1": "77777777-7777-4777-8777-777777777777",
}


def load_yaml(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def substitute(obj: Any) -> Any:
    if isinstance(obj, str):
        for key, val in PLACEHOLDERS.items():
            obj = obj.replace("{" + key + "}", val)
        return obj
    if isinstance(obj, list):
        return [substitute(v) for v in obj]
    if isinstance(obj, dict):
        return {k: substitute(v) for k, v in obj.items()}
    return obj


def build_context(fixture: Dict[str, Any], path_params: Dict[str, str]) -> Dict[str, Any]:
    assets = {v["id"]: v for v in fixture.get("assets", {}).values()}
    clips = {v["id"]: v for v in fixture.get("clips", {}).values()}
    audio_clips = {v["id"]: v for v in fixture.get("audio_clips", {}).values()}

    # Precompute end times
    for clip in clips.values():
        clip["end_ms"] = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
    for clip in audio_clips.values():
        clip["end_ms"] = clip.get("start_ms", 0) + clip.get("duration_ms", 0)

    return {
        "fixture": fixture,
        "assets_by_id": assets,
        "clips_by_id": clips,
        "audio_clips_by_id": audio_clips,
        "path_params": path_params,
        "capabilities": fixture.get("capabilities", {}),
    }


def resolve_ref(spec: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/components/"):
        raise ValueError(f"Unsupported $ref: {ref}")
    parts = ref.strip("#/ ").split("/")
    node: Any = spec
    for p in parts:
        node = node[p]
    return node


def match_path(template: str, path: str) -> Optional[Dict[str, str]]:
    pattern = re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", template)
    pattern = "^" + pattern + "$"
    m = re.match(pattern, path)
    if not m:
        return None
    return m.groupdict()


def find_operation(spec: Dict[str, Any], method: str, path: str) -> Optional[Dict[str, Any]]:
    for template, ops in spec.get("paths", {}).items():
        params = match_path(template, path)
        if params is None:
            continue
        op = ops.get(method.lower())
        if not op:
            continue
        return {"op": op, "path_params": params}
    return None


def normalize_path(spec: Dict[str, Any], path: str) -> str:
    servers = spec.get("servers", []) or []
    for server in servers:
        base = (server.get("url") or "").rstrip("/")
        if base and path.startswith(base + "/"):
            return path[len(base):]
        if base and path == base:
            return "/"
    return path


def when_true(expr: Optional[str], instance: Dict[str, Any]) -> bool:
    if not expr:
        return True
    expr = expr.strip()
    # Supported patterns: "field != null", "field == 'value'", "field != 'value'"
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*!=\s*null$", expr)
    if m:
        return instance.get(m.group(1)) is not None
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*==\s*'([^']+)'$", expr)
    if m:
        return instance.get(m.group(1)) == m.group(2)
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*!=\s*'([^']+)'$", expr)
    if m:
        return instance.get(m.group(1)) != m.group(2)
    return True


def has_overlap(r1_start: int, r1_end: int, r2_start: int, r2_end: int) -> bool:
    return r1_start < r2_end and r1_end > r2_start


def eval_constraint(constraint_id: str, instance: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    assets = ctx["assets_by_id"]
    clips = ctx["clips_by_id"]
    audio_clips = ctx["audio_clips_by_id"]
    caps = ctx["capabilities"]
    path_params = ctx["path_params"]

    def get_asset_duration(asset_id: Optional[str]) -> Optional[int]:
        if asset_id is None:
            return None
        asset = assets.get(asset_id)
        return asset.get("duration_ms") if asset else None

    if constraint_id == "CLIP_IN_OUT_RANGE":
        out_point = instance.get("out_point_ms")
        if out_point is None:
            return True
        return out_point > instance.get("in_point_ms", 0)

    if constraint_id == "CLIP_IN_OUT_ASSET_RANGE":
        asset_id = instance.get("asset_id")
        clip_id = instance.get("clip_id") or path_params.get("clip_id")
        duration = None
        if asset_id:
            duration = get_asset_duration(asset_id)
        elif clip_id:
            clip = clips.get(clip_id)
            if clip:
                duration = get_asset_duration(clip.get("asset_id"))
        if duration is None:
            return True
        out_point = instance.get("out_point_ms")
        in_point = instance.get("in_point_ms", 0)
        return in_point >= 0 and (out_point is None or out_point <= duration)

    if constraint_id == "CLIP_NO_OVERLAP":
        layer_id = instance.get("layer_id")
        if not layer_id:
            return True
        start = instance.get("start_ms", 0)
        end = start + instance.get("duration_ms", 0)
        if end <= start:
            return True
        for clip in clips.values():
            if clip.get("layer_id") != layer_id:
                continue
            if has_overlap(start, end, clip.get("start_ms", 0), clip.get("end_ms", 0)):
                return False
        return True

    if constraint_id == "AUDIO_IN_OUT_RANGE":
        out_point = instance.get("out_point_ms")
        if out_point is None:
            return True
        return out_point > instance.get("in_point_ms", 0)

    if constraint_id == "AUDIO_IN_OUT_ASSET_RANGE":
        asset_id = instance.get("asset_id")
        clip_id = path_params.get("clip_id") or instance.get("clip_id")
        duration = None
        if asset_id:
            duration = get_asset_duration(asset_id)
        elif clip_id:
            clip = audio_clips.get(clip_id)
            if clip:
                duration = get_asset_duration(clip.get("asset_id"))
        if duration is None:
            return True
        out_point = instance.get("out_point_ms")
        in_point = instance.get("in_point_ms", 0)
        return in_point >= 0 and (out_point is None or out_point <= duration)

    if constraint_id == "AUDIO_NO_OVERLAP":
        track_id = instance.get("track_id")
        if not track_id:
            return True
        start = instance.get("start_ms", 0)
        end = start + instance.get("duration_ms", 0)
        if end <= start:
            return True
        for clip in audio_clips.values():
            if clip.get("track_id") != track_id:
                continue
            if has_overlap(start, end, clip.get("start_ms", 0), clip.get("end_ms", 0)):
                return False
        return True

    if constraint_id == "KEYFRAME_WITHIN_CLIP":
        clip_id = path_params.get("clip_id") or instance.get("clip_id")
        if not clip_id:
            return True
        clip = clips.get(clip_id)
        if not clip:
            return True
        return clip.get("start_ms", 0) <= instance.get("time_ms", 0) <= clip.get("end_ms", 0)

    if constraint_id == "VOLUME_KEYFRAME_WITHIN_CLIP":
        clip_id = path_params.get("clip_id")
        if not clip_id:
            return True
        clip = audio_clips.get(clip_id)
        if not clip:
            return True
        return clip.get("start_ms", 0) <= instance.get("time_ms", 0) <= clip.get("end_ms", 0)

    if constraint_id == "FONT_FAMILY_SUPPORTED":
        allowed = set(caps.get("font_families", []))
        return instance.get("font_family") in allowed

    if constraint_id == "TRANSITION_CUT_DURATION":
        return instance.get("duration_ms") == 0

    if constraint_id == "TRANSITION_NONCUT_DURATION":
        duration = instance.get("duration_ms")
        if duration is None:
            return False
        return 100 <= duration <= 2000

    # Unknown constraint: treat as pass for now
    return True


def traverse_schema(spec: Dict[str, Any], schema: Dict[str, Any], instance: Any, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if instance is None:
        return None

    if "$ref" in schema:
        schema = resolve_ref(spec, schema["$ref"])

    # Evaluate x-constraints at this level
    constraints = schema.get("x-constraints", [])
    for c in constraints:
        when_expr = c.get("when")
        if isinstance(instance, dict) and not when_true(when_expr, instance):
            continue
        if isinstance(instance, dict) and not eval_constraint(c.get("id", ""), instance, ctx):
            return c

    if "oneOf" in schema and isinstance(instance, dict):
        # Select a matching subschema by const type if possible
        for sub in schema["oneOf"]:
            sub_schema = resolve_ref(spec, sub["$ref"]) if "$ref" in sub else sub
            props = sub_schema.get("properties", {})
            const_type = props.get("type", {}).get("const")
            if const_type and instance.get("type") == const_type:
                return traverse_schema(spec, sub_schema, instance, ctx)
        # Fallback: check all
        for sub in schema["oneOf"]:
            sub_schema = resolve_ref(spec, sub["$ref"]) if "$ref" in sub else sub
            violation = traverse_schema(spec, sub_schema, instance, ctx)
            if violation:
                return violation

    if "properties" in schema and isinstance(instance, dict):
        for prop, prop_schema in schema["properties"].items():
            if prop not in instance:
                continue
            violation = traverse_schema(spec, prop_schema, instance[prop], ctx)
            if violation:
                return violation

    if schema.get("type") == "array" and isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema:
            for item in instance:
                violation = traverse_schema(spec, item_schema, item, ctx)
                if violation:
                    return violation

    return None


def main() -> int:
    spec = load_yaml(OPENAPI_PATH)
    cases = sorted(glob.glob(os.path.join(CASES_DIR, "*.yaml")))
    if not cases:
        print("No cases found.")
        return 1

    failed = 0
    for case_path in cases:
        case = substitute(load_yaml(case_path))
        case_id = case.get("id", os.path.basename(case_path))
        fixture_name = case.get("context", {}).get("fixture", "base.yaml")
        fixture = substitute(load_yaml(os.path.join(FIXTURES_DIR, fixture_name)))

        req = case.get("request", {})
        method = req.get("method", "GET")
        path = req.get("path", "/")
        body = req.get("body")

        normalized_path = normalize_path(spec, path)
        op_match = find_operation(spec, method, normalized_path)
        if not op_match:
            print(f"FAIL {case_id}: endpoint not found for {method} {path}")
            failed += 1
            continue

        op = op_match["op"]
        path_params = op_match["path_params"]
        ctx = build_context(fixture, path_params)

        req_body = op.get("requestBody", {})
        content = req_body.get("content", {}).get("application/json", {})
        schema = content.get("schema")
        if schema is None:
            print(f"FAIL {case_id}: no requestBody schema")
            failed += 1
            continue

        violation = traverse_schema(spec, schema, body, ctx)
        expect = case.get("expect", {})
        expected_error = expect.get("error_code")

        if case.get("kind") == "pass":
            if violation:
                print(f"FAIL {case_id}: unexpected violation {violation.get('error_code')}")
                failed += 1
            else:
                print(f"PASS {case_id}")
            continue

        # fail case
        if not violation:
            print(f"FAIL {case_id}: expected error {expected_error}, got none")
            failed += 1
            continue
        if expected_error and violation.get("error_code") != expected_error:
            print(f"FAIL {case_id}: expected {expected_error}, got {violation.get('error_code')}")
            failed += 1
            continue
        expected_fix = expect.get("suggested_fix")
        if expected_fix and violation.get("suggested_fix") != expected_fix:
            print(f"FAIL {case_id}: suggested_fix mismatch")
            failed += 1
            continue
        print(f"PASS {case_id}")

    if failed:
        print(f"\nFAILED: {failed} case(s)")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
