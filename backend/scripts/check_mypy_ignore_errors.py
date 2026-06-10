#!/usr/bin/env python3
"""Check that mypy ignore_errors overrides have not increased.

This script counts the number of modules listed under [[tool.mypy.overrides]]
with ignore_errors = true in pyproject.toml and compares against the baseline.
If the count has increased (new modules added), the script exits with code 1.

Baseline: 49 modules (as of 2026-06-10, Issue #276).
"""

import sys
import tomllib
from pathlib import Path

# Baseline module count — update only when modules are REMOVED.
BASELINE_COUNT = 49

pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

with open(pyproject_path, "rb") as f:
    config = tomllib.load(f)

mypy_overrides = config.get("tool", {}).get("mypy", {}).get("overrides", [])

ignore_errors_modules: list[str] = []
for override in mypy_overrides:
    if override.get("ignore_errors"):
        modules = override.get("module", [])
        if isinstance(modules, list):
            ignore_errors_modules.extend(modules)
        elif isinstance(modules, str):
            ignore_errors_modules.append(modules)

current_count = len(ignore_errors_modules)

if current_count > BASELINE_COUNT:
    print(
        f"ERROR: mypy ignore_errors module count increased: "
        f"baseline={BASELINE_COUNT}, current={current_count}",
        file=sys.stderr,
    )
    print(
        "Do not add new modules to the ignore_errors list. "
        "Fix the type errors instead.",
        file=sys.stderr,
    )
    sys.exit(1)

print(
    f"OK: mypy ignore_errors module count is {current_count} "
    f"(baseline={BASELINE_COUNT})"
)
