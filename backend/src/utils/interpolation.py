"""Interpolation utilities for keyframe animation.

Provides easing functions and an interpolate() function matching Remotion's API.
Used by both the FFmpeg filter generator and the frame sampler to compute
animated property values at any point in time.

Usage:
    from src.utils.interpolation import interpolate, Easing

    # Linear interpolation
    value = interpolate(frame=50, input_range=[0, 100], output_range=[0, 1])

    # With easing
    value = interpolate(
        frame=50,
        input_range=[0, 100],
        output_range=[0, 1],
        easing=Easing.ease_in_out,
    )
"""

import math
from enum import Enum
from typing import Callable


# =============================================================================
# Easing Functions
# =============================================================================


def linear(t: float) -> float:
    """Linear easing (no easing)."""
    return t


def ease_in(t: float) -> float:
    """Ease in (cubic)."""
    return t * t * t


def ease_out(t: float) -> float:
    """Ease out (cubic)."""
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    """Ease in-out (cubic)."""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - (-2 * t + 2) ** 3 / 2


def ease_in_quad(t: float) -> float:
    """Ease in (quadratic)."""
    return t * t


def ease_out_quad(t: float) -> float:
    """Ease out (quadratic)."""
    return 1 - (1 - t) * (1 - t)


def ease_in_out_quad(t: float) -> float:
    """Ease in-out (quadratic)."""
    if t < 0.5:
        return 2 * t * t
    else:
        return 1 - (-2 * t + 2) ** 2 / 2


def ease_in_sine(t: float) -> float:
    """Ease in (sine)."""
    return 1 - math.cos((t * math.pi) / 2)


def ease_out_sine(t: float) -> float:
    """Ease out (sine)."""
    return math.sin((t * math.pi) / 2)


def ease_in_out_sine(t: float) -> float:
    """Ease in-out (sine)."""
    return -(math.cos(math.pi * t) - 1) / 2


def ease_in_expo(t: float) -> float:
    """Ease in (exponential)."""
    return 0 if t == 0 else 2 ** (10 * t - 10)


def ease_out_expo(t: float) -> float:
    """Ease out (exponential)."""
    return 1 if t == 1 else 1 - 2 ** (-10 * t)


def ease_in_out_expo(t: float) -> float:
    """Ease in-out (exponential)."""
    if t == 0:
        return 0
    if t == 1:
        return 1
    if t < 0.5:
        return 2 ** (20 * t - 10) / 2
    return (2 - 2 ** (-20 * t + 10)) / 2


def ease_in_back(t: float) -> float:
    """Ease in with overshoot."""
    c1 = 1.70158
    c3 = c1 + 1
    return c3 * t * t * t - c1 * t * t


def ease_out_back(t: float) -> float:
    """Ease out with overshoot."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_in_out_back(t: float) -> float:
    """Ease in-out with overshoot."""
    c1 = 1.70158
    c2 = c1 * 1.525
    if t < 0.5:
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2
    return ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2


def bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """Create a cubic bezier easing function.

    Args:
        x1, y1: First control point
        x2, y2: Second control point

    Returns:
        Easing function (t -> value)
    """
    def _bezier(t: float) -> float:
        # Newton-Raphson to find t for x
        epsilon = 1e-6
        t_approx = t

        for _ in range(8):
            # Calculate x at t_approx
            x = (
                3 * (1 - t_approx) ** 2 * t_approx * x1
                + 3 * (1 - t_approx) * t_approx ** 2 * x2
                + t_approx ** 3
            )
            if abs(x - t) < epsilon:
                break

            # Derivative
            dx = (
                3 * (1 - t_approx) ** 2 * x1
                + 6 * (1 - t_approx) * t_approx * (x2 - x1)
                + 3 * t_approx ** 2 * (1 - x2)
            )
            if abs(dx) < epsilon:
                break

            t_approx -= (x - t) / dx

        # Calculate y at t_approx
        return (
            3 * (1 - t_approx) ** 2 * t_approx * y1
            + 3 * (1 - t_approx) * t_approx ** 2 * y2
            + t_approx ** 3
        )

    return _bezier


class Easing:
    """Collection of easing functions."""

    linear = linear
    ease_in = ease_in
    ease_out = ease_out
    ease_in_out = ease_in_out
    ease_in_quad = ease_in_quad
    ease_out_quad = ease_out_quad
    ease_in_out_quad = ease_in_out_quad
    ease_in_sine = ease_in_sine
    ease_out_sine = ease_out_sine
    ease_in_out_sine = ease_in_out_sine
    ease_in_expo = ease_in_expo
    ease_out_expo = ease_out_expo
    ease_in_out_expo = ease_in_out_expo
    ease_in_back = ease_in_back
    ease_out_back = ease_out_back
    ease_in_out_back = ease_in_out_back
    bezier = staticmethod(bezier)

    # Named presets matching common CSS easings
    css_ease = staticmethod(bezier(0.25, 0.1, 0.25, 1.0))
    css_ease_in = staticmethod(bezier(0.42, 0, 1.0, 1.0))
    css_ease_out = staticmethod(bezier(0, 0, 0.58, 1.0))
    css_ease_in_out = staticmethod(bezier(0.42, 0, 0.58, 1.0))


# Easing name -> function lookup for JSON/string-based configuration
EASING_FUNCTIONS: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "ease_in": ease_in,
    "ease_out": ease_out,
    "ease_in_out": ease_in_out,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_in_sine": ease_in_sine,
    "ease_out_sine": ease_out_sine,
    "ease_in_out_sine": ease_in_out_sine,
    "ease_in_expo": ease_in_expo,
    "ease_out_expo": ease_out_expo,
    "ease_in_out_expo": ease_in_out_expo,
    "ease_in_back": ease_in_back,
    "ease_out_back": ease_out_back,
    "ease_in_out_back": ease_in_out_back,
}


def get_easing_function(name: str) -> Callable[[float], float]:
    """Get an easing function by name.

    Args:
        name: Easing function name (e.g., "ease_in_out", "linear")

    Returns:
        Easing function

    Raises:
        ValueError: If the easing name is not recognized
    """
    fn = EASING_FUNCTIONS.get(name)
    if fn is None:
        raise ValueError(
            f"Unknown easing function: {name}. "
            f"Available: {', '.join(EASING_FUNCTIONS.keys())}"
        )
    return fn


# =============================================================================
# Core Interpolation
# =============================================================================


class ExtrapolateType(Enum):
    """How to handle values outside the input range."""

    CLAMP = "clamp"
    EXTEND = "extend"
    IDENTITY = "identity"


def interpolate(
    frame: float,
    input_range: list[float],
    output_range: list[float],
    *,
    easing: Callable[[float], float] = linear,
    extrapolate_left: ExtrapolateType = ExtrapolateType.CLAMP,
    extrapolate_right: ExtrapolateType = ExtrapolateType.CLAMP,
) -> float:
    """Interpolate a value based on input/output ranges with optional easing.

    Equivalent to Remotion's interpolate() function.

    Args:
        frame: Current frame or time value
        input_range: Input range [start, end] or multi-point [a, b, c, ...]
        output_range: Output range matching input_range length
        easing: Easing function (default: linear)
        extrapolate_left: How to handle values below input_range[0]
        extrapolate_right: How to handle values above input_range[-1]

    Returns:
        Interpolated output value

    Examples:
        # Simple 0-1 mapping
        interpolate(50, [0, 100], [0, 1])  # -> 0.5

        # With easing
        interpolate(50, [0, 100], [0, 1], easing=Easing.ease_in_out)

        # Multi-point
        interpolate(75, [0, 50, 100], [0, 1, 0])  # -> 0.5
    """
    if len(input_range) != len(output_range):
        raise ValueError("input_range and output_range must have the same length")
    if len(input_range) < 2:
        raise ValueError("input_range must have at least 2 values")

    # Validate input_range is monotonically increasing
    for i in range(1, len(input_range)):
        if input_range[i] <= input_range[i - 1]:
            raise ValueError("input_range must be monotonically increasing")

    # Find the segment
    if frame <= input_range[0]:
        # Before range
        if extrapolate_left == ExtrapolateType.CLAMP:
            return output_range[0]
        elif extrapolate_left == ExtrapolateType.IDENTITY:
            return frame
        # EXTEND: fall through to use first segment

    if frame >= input_range[-1]:
        # After range
        if extrapolate_right == ExtrapolateType.CLAMP:
            return output_range[-1]
        elif extrapolate_right == ExtrapolateType.IDENTITY:
            return frame
        # EXTEND: fall through to use last segment

    # Find the correct segment
    segment_idx = 0
    for i in range(1, len(input_range)):
        if frame <= input_range[i]:
            segment_idx = i - 1
            break
    else:
        segment_idx = len(input_range) - 2

    # Calculate t within this segment
    seg_start = input_range[segment_idx]
    seg_end = input_range[segment_idx + 1]
    seg_range = seg_end - seg_start

    if seg_range == 0:
        t = 0
    else:
        t = (frame - seg_start) / seg_range

    # Apply easing
    t_eased = easing(t)

    # Interpolate output
    out_start = output_range[segment_idx]
    out_end = output_range[segment_idx + 1]

    return out_start + (out_end - out_start) * t_eased


def interpolate_keyframes(
    time_ms: float,
    keyframes: list[dict],
    property_name: str,
    *,
    easing_name: str = "linear",
    default_value: float = 0.0,
) -> float:
    """Interpolate a property value from a list of keyframes.

    Convenience function for working with douga's keyframe format.

    Args:
        time_ms: Current time in milliseconds (relative to clip start)
        keyframes: List of keyframe dicts with time_ms and transform/opacity
        property_name: Property to interpolate ("x", "y", "scale", "rotation", "opacity")
        easing_name: Easing function name
        default_value: Value to return if no keyframes

    Returns:
        Interpolated value at the given time
    """
    if not keyframes:
        return default_value

    # Sort keyframes by time
    sorted_kf = sorted(keyframes, key=lambda kf: kf.get("time_ms", 0))

    # Extract input/output ranges
    input_range = []
    output_range = []

    for kf in sorted_kf:
        input_range.append(float(kf.get("time_ms", 0)))

        if property_name == "opacity":
            output_range.append(float(kf.get("opacity", default_value)))
        else:
            transform = kf.get("transform", {})
            output_range.append(float(transform.get(property_name, default_value)))

    if len(input_range) < 2:
        return output_range[0] if output_range else default_value

    easing_fn = get_easing_function(easing_name)

    return interpolate(
        time_ms,
        input_range,
        output_range,
        easing=easing_fn,
        extrapolate_left=ExtrapolateType.CLAMP,
        extrapolate_right=ExtrapolateType.CLAMP,
    )


def interpolate_all_properties(
    time_ms: float,
    keyframes: list[dict],
    *,
    easing_name: str = "linear",
    default_transform: dict | None = None,
    default_opacity: float = 1.0,
) -> dict:
    """Interpolate all transform properties and opacity from keyframes.

    Args:
        time_ms: Current time in milliseconds (relative to clip start)
        keyframes: List of keyframe dicts
        easing_name: Easing function name
        default_transform: Default transform values
        default_opacity: Default opacity

    Returns:
        Dict with interpolated x, y, scale, rotation, opacity
    """
    defaults = default_transform or {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}

    return {
        "x": interpolate_keyframes(
            time_ms, keyframes, "x",
            easing_name=easing_name, default_value=defaults.get("x", 0),
        ),
        "y": interpolate_keyframes(
            time_ms, keyframes, "y",
            easing_name=easing_name, default_value=defaults.get("y", 0),
        ),
        "scale": interpolate_keyframes(
            time_ms, keyframes, "scale",
            easing_name=easing_name, default_value=defaults.get("scale", 1.0),
        ),
        "rotation": interpolate_keyframes(
            time_ms, keyframes, "rotation",
            easing_name=easing_name, default_value=defaults.get("rotation", 0),
        ),
        "opacity": interpolate_keyframes(
            time_ms, keyframes, "opacity",
            easing_name=easing_name, default_value=default_opacity,
        ),
    }
