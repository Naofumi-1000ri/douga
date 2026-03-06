"""Shared Pydantic validators for schema fields."""

from typing import Any

# Mapping from string font weight names to numeric values.
FONT_WEIGHT_NAME_MAP: dict[str, int] = {
    "thin": 100,
    "extralight": 200,
    "ultralight": 200,
    "light": 300,
    "normal": 400,
    "regular": 400,
    "medium": 500,
    "semibold": 600,
    "demibold": 600,
    "bold": 700,
    "extrabold": 800,
    "ultrabold": 800,
    "black": 900,
    "heavy": 900,
}


def normalize_font_weight(value: Any) -> int:
    """Convert font_weight to an integer (100-900).

    Accepts:
      - int: 100, 200, ..., 900 (passed through with validation)
      - str numeric: "700" -> 700
      - str name: "bold" -> 700, "normal" -> 400, etc.

    Raises ValueError for unrecognized strings or out-of-range integers.
    """
    if value is None:
        return value  # type: ignore[return-value]

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        # Try numeric string first
        if value.isdigit():
            return int(value)
        # Try named weight
        name = value.strip().lower()
        if name in FONT_WEIGHT_NAME_MAP:
            return FONT_WEIGHT_NAME_MAP[name]
        raise ValueError(
            f"Invalid font_weight string: '{value}'. "
            f"Accepted names: {', '.join(sorted(FONT_WEIGHT_NAME_MAP.keys()))}. "
            f"Or use an integer 100-900."
        )

    # Float that is a whole number (e.g. 700.0 from JSON)
    if isinstance(value, float) and value == int(value):
        return int(value)

    raise ValueError(
        f"font_weight must be an integer (100-900) or a weight name string, got {type(value).__name__}: {value}"
    )
