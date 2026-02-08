#!/usr/bin/env python3
"""Generate Pydantic schemas and capabilities JSON from effects_spec.yaml.

Usage:
    uv run python backend/scripts/generate_effects.py

Outputs:
    backend/src/schemas/effects_generated.py  -- Pydantic models
    (capabilities dict is generated at import-time, not written to file)
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    # Fallback: parse simple YAML manually (PyYAML may not be installed)
    yaml = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent  # backend/
SPEC_PATH = REPO_ROOT / "specs" / "effects_spec.yaml"
OUTPUT_PATH = REPO_ROOT / "src" / "schemas" / "effects_generated.py"


# ---------------------------------------------------------------------------
# Minimal YAML parser (no dependency on PyYAML)
# ---------------------------------------------------------------------------

def _parse_yaml_simple(text: str) -> dict:
    """Parse a subset of YAML sufficient for effects_spec.yaml.

    Supports:
    - Top-level and nested mappings (detected by indentation)
    - Scalars: strings (quoted or unquoted), numbers, booleans
    - Lists of scalars (using ``- item`` syntax)
    - Comments (lines starting with #, or inline # after value)

    This is intentionally minimal. If the spec becomes more complex,
    install PyYAML.
    """
    lines = text.split("\n")
    return _parse_mapping(lines, 0, 0)[0]


def _strip_comment(s: str) -> str:
    """Remove trailing inline comment, respecting quoted strings."""
    in_quote: str | None = None
    for i, ch in enumerate(s):
        if ch in ('"', "'") and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif ch == "#" and in_quote is None:
            return s[:i].rstrip()
    return s


def _parse_scalar(raw: str):  # noqa: ANN201
    """Parse a scalar value."""
    raw = _strip_comment(raw).strip()
    if not raw:
        return None
    # Quoted string
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    # Boolean
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    if raw.lower() in ("null", "none", "~"):
        return None
    # Number
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    return raw


def _indent_level(line: str) -> int:
    return len(line) - len(line.lstrip())


def _parse_mapping(lines: list[str], start: int, base_indent: int) -> tuple[dict, int]:
    """Parse a YAML mapping starting at *start* with *base_indent*."""
    result: dict = {}
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = _indent_level(line)
        if indent < base_indent:
            break
        if indent > base_indent:
            # This shouldn't happen at the top level — skip
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        colon_idx = stripped.index(":")
        key = stripped[:colon_idx].strip().strip('"').strip("'")
        rest = stripped[colon_idx + 1 :].strip()
        rest = _strip_comment(rest)
        if rest == "" or rest is None:
            # Nested mapping or list
            # Peek ahead to determine child indent
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nxt_s = nxt.strip()
                if nxt_s and not nxt_s.startswith("#"):
                    break
                j += 1
            if j < len(lines):
                child_indent = _indent_level(lines[j])
                child_stripped = lines[j].strip()
                if child_stripped.startswith("- "):
                    # It's a list
                    lst, i = _parse_list(lines, j, child_indent)
                    result[key] = lst
                else:
                    mapping, i = _parse_mapping(lines, j, child_indent)
                    result[key] = mapping
            else:
                result[key] = {}
                i = j
        else:
            result[key] = _parse_scalar(rest)
            i += 1
    return result, i


def _parse_list(lines: list[str], start: int, base_indent: int) -> tuple[list, int]:
    """Parse a YAML list starting at *start*."""
    result: list = []
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = _indent_level(line)
        if indent < base_indent:
            break
        if stripped.startswith("- "):
            val = _parse_scalar(stripped[2:])
            result.append(val)
            i += 1
        else:
            break
    return result, i


# ---------------------------------------------------------------------------
# Load spec
# ---------------------------------------------------------------------------

def load_spec(path: Path | None = None) -> dict:
    """Load and return the parsed effects_spec.yaml."""
    p = path or SPEC_PATH
    text = p.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text)
    return _parse_yaml_simple(text)


# ---------------------------------------------------------------------------
# Capabilities generation
# ---------------------------------------------------------------------------

def generate_capabilities(spec: dict) -> dict:
    """Generate the capabilities dict fragment for the /capabilities endpoint.

    Returns a dict with keys:
        supported_effects: list[str]
        effect_params: dict  (effect_name -> param details)
    """
    effects = spec.get("effects", {})
    supported: list[str] = []
    params_info: dict = {}

    # Effects with nested params (like chroma_key) go into effect_params.
    # Single-value effects (opacity, blend_mode, etc.) are listed in
    # supported_effects but their schema is simpler.
    for name, edef in effects.items():
        supported.append(name)
        params = edef.get("params", {})
        if len(params) == 1 and "value" in params:
            # Single-value effect — expose directly
            p = params["value"]
            info: dict = {"type": p.get("type", "string")}
            if "minimum" in p:
                info["min"] = p["minimum"]
            if "maximum" in p:
                info["max"] = p["maximum"]
            if "enum" in p:
                info["enum"] = p["enum"]
            if "default" in p:
                info["default"] = p["default"]
            params_info[name] = info
        else:
            # Multi-param effect
            ep: dict = {}
            for pname, pdef in params.items():
                if pname == "enabled":
                    continue  # skip 'enabled' from param listing
                pinfo: dict = {"type": pdef.get("type", "string")}
                if "format" in pdef:
                    pinfo["format"] = pdef["format"]
                if "minimum" in pdef:
                    pinfo["min"] = pdef["minimum"]
                if "maximum" in pdef:
                    pinfo["max"] = pdef["maximum"]
                if "enum" in pdef:
                    pinfo["enum"] = pdef["enum"]
                if "default" in pdef:
                    pinfo["default"] = pdef["default"]
                ep[pname] = pinfo
            params_info[name] = ep

    return {
        "supported_effects": supported,
        "effect_params": params_info,
    }


# ---------------------------------------------------------------------------
# Pydantic code generation
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "boolean": "bool",
    "string": "str",
    "number": "float",
    "integer": "int",
}


def _field_kwargs(pdef: dict) -> str:
    """Build Field(...) kwargs string from a param definition."""
    parts: list[str] = []
    if "default" in pdef:
        default = pdef["default"]
        if isinstance(default, str):
            parts.append(f'default="{default}"')
        elif isinstance(default, bool):
            parts.append(f"default={default}")
        else:
            parts.append(f"default={default}")
    if "minimum" in pdef:
        parts.append(f"ge={pdef['minimum']}")
    if "maximum" in pdef:
        parts.append(f"le={pdef['maximum']}")
    if "pattern" in pdef:
        parts.append(f'pattern=r"{pdef["pattern"]}"')
    if "description" in pdef:
        parts.append(f'description="{pdef["description"]}"')
    return ", ".join(parts)


def generate_pydantic_code(spec: dict) -> str:
    """Generate Pydantic model source code from the spec."""
    effects = spec.get("effects", {})
    lines: list[str] = []

    lines.append('"""Auto-generated effects schemas from effects_spec.yaml.')
    lines.append("")
    lines.append("DO NOT EDIT MANUALLY.")
    lines.append("Regenerate with: uv run python backend/scripts/generate_effects.py")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from pydantic import BaseModel, Field")
    lines.append("")
    lines.append("")

    # Track which effects are multi-param (generate their own model)
    multi_param_effects: list[tuple[str, str]] = []  # (effect_name, class_name)
    single_value_fields: list[str] = []  # lines for single-value fields in Effects

    for effect_name, edef in effects.items():
        params = edef.get("params", {})
        description = edef.get("description", "")

        if len(params) == 1 and "value" in params:
            # Single-value effect -> becomes a field on Effects model directly
            pdef = params["value"]
            py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
            kwargs = _field_kwargs(pdef)
            single_value_fields.append(
                f'    {effect_name}: {py_type} = Field({kwargs})'
            )
        else:
            # Multi-param effect -> gets its own model
            class_name = "".join(w.capitalize() for w in effect_name.split("_")) + "Effect"
            multi_param_effects.append((effect_name, class_name))

            lines.append(f"class {class_name}(BaseModel):")
            lines.append(f'    """{description}"""')
            lines.append("")
            for pname, pdef in params.items():
                py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
                kwargs = _field_kwargs(pdef)
                lines.append(f"    {pname}: {py_type} = Field({kwargs})")
            lines.append("")
            lines.append("")

    # Generate the unified Effects model
    lines.append("class Effects(BaseModel):")
    lines.append('    """Unified effects model for clips.')
    lines.append("")
    lines.append("    Generated from effects_spec.yaml. Contains all supported effects.")
    lines.append('    """')
    lines.append("")

    # Multi-param effects as optional nested objects
    for effect_name, class_name in multi_param_effects:
        lines.append(f"    {effect_name}: {class_name} | None = None")

    # Single-value effects as direct fields
    for field_line in single_value_fields:
        lines.append(field_line)

    lines.append("")
    lines.append("")

    # Generate UpdateClipEffectsRequest (flat API format for backward compatibility)
    lines.append("class GeneratedUpdateClipEffectsRequest(BaseModel):")
    lines.append('    """Flat effects update request (API-facing, backward compatible).')
    lines.append("")
    lines.append("    Generated from effects_spec.yaml.")
    lines.append('    """')
    lines.append("")

    for effect_name, edef in effects.items():
        params = edef.get("params", {})
        if len(params) == 1 and "value" in params:
            # Single-value: directly as optional field
            pdef = params["value"]
            py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
            kwargs_parts: list[str] = ["default=None"]
            if "minimum" in pdef:
                kwargs_parts.append(f"ge={pdef['minimum']}")
            if "maximum" in pdef:
                kwargs_parts.append(f"le={pdef['maximum']}")
            lines.append(
                f"    {effect_name}: {py_type} | None = Field({', '.join(kwargs_parts)})"
            )
        else:
            # Multi-param: flatten with prefix
            for pname, pdef in params.items():
                flat_name = f"{effect_name}_{pname}"
                py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
                kwargs_parts = ["default=None"]
                if "minimum" in pdef:
                    kwargs_parts.append(f"ge={pdef['minimum']}")
                if "maximum" in pdef:
                    kwargs_parts.append(f"le={pdef['maximum']}")
                if "pattern" in pdef:
                    kwargs_parts.append(f'pattern=r"{pdef["pattern"]}"')
                lines.append(
                    f"    {flat_name}: {py_type} | None = Field({', '.join(kwargs_parts)})"
                )

    lines.append("")
    lines.append("")

    # Generate EffectsDetails (flat response format for L3 clip details)
    lines.append("class GeneratedEffectsDetails(BaseModel):")
    lines.append('    """Flat effects response model (for L3 clip details).')
    lines.append("")
    lines.append("    Generated from effects_spec.yaml.")
    lines.append('    """')
    lines.append("")

    for effect_name, edef in effects.items():
        params = edef.get("params", {})
        if len(params) == 1 and "value" in params:
            pdef = params["value"]
            py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
            kwargs = _field_kwargs(pdef)
            lines.append(f"    {effect_name}: {py_type} = Field({kwargs})")
        else:
            for pname, pdef in params.items():
                flat_name = f"{effect_name}_{pname}"
                py_type = _TYPE_MAP.get(pdef.get("type", "string"), "str")
                default = pdef.get("default")
                kwargs_parts = []
                if isinstance(default, str):
                    kwargs_parts.append(f'default="{default}"')
                elif isinstance(default, bool):
                    kwargs_parts.append(f"default={default}")
                elif default is not None:
                    kwargs_parts.append(f"default={default}")
                else:
                    kwargs_parts.append("default=None")
                if "minimum" in pdef:
                    kwargs_parts.append(f"ge={pdef['minimum']}")
                if "maximum" in pdef:
                    kwargs_parts.append(f"le={pdef['maximum']}")
                lines.append(
                    f"    {flat_name}: {py_type} = Field({', '.join(kwargs_parts)})"
                )

    lines.append("")
    lines.append("")

    # Generate capabilities loading function
    lines.append("# Capabilities data (generated from spec)")
    lines.append("EFFECTS_CAPABILITIES: dict = " + _format_dict(generate_capabilities(spec)))
    lines.append("")

    return "\n".join(lines)


def _format_dict(d: dict, indent: int = 0) -> str:
    """Format a dict as a Python literal string."""
    return json.dumps(d, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    spec = load_spec()
    code = generate_pydantic_code(spec)

    # Validate generated code syntax
    import ast
    try:
        ast.parse(code)
    except SyntaxError as e:
        print(f"ERROR: Generated code has syntax error: {e}", file=sys.stderr)
        print("--- Generated code ---", file=sys.stderr)
        for i, line in enumerate(code.split("\n"), 1):
            print(f"{i:4d}: {line}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(code, encoding="utf-8")
    print(f"Generated: {OUTPUT_PATH}")
    print(f"  Effects: {list(spec.get('effects', {}).keys())}")

    # Also print capabilities for verification
    caps = generate_capabilities(spec)
    print(f"  Supported effects: {caps['supported_effects']}")
    print("Done.")


if __name__ == "__main__":
    main()
